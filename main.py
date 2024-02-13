import os
from datetime import datetime, timedelta

from credentials import IS_BACKTESTING
from lumibot.backtesting import PolygonDataBacktesting
from lumibot.entities import Asset, TradingFee
from lumibot.strategies.strategy import Strategy
from lumibot.traders import Trader

"""
Strategy Description

This strategy will use a percentage of the account to buy call options on a predetermined schedule.
The call options will be sold when they are about to expire, and new call options will be bought. The call
options will be bought with a strike price that is a certain percentage above the
current price of the underlying asset. The remaining account balance will be used to buy a fixed income ETF.

"""


class OptionsRollingCalls(Strategy):
    parameters = {
        "underlying_asset": Asset(
            symbol="QQQ", asset_type=Asset.AssetType.STOCK
        ),  # The underlying asset we will be using
        "fixed_income_symbol": "USFR",  # The fixed income ETF that we will be using
        "pct_call_out_of_money": 0.0,  # How far out of the money the call should be
        # How much of the portfolio should be in call options
        "pct_portfolio_in_calls": 0.25,
        "days_to_expiry": 10,  # How many days until the call option expires when we buy it
        "days_before_expiry_to_sell": 1,  # How many days before expiry to sell the call (if None, hold until expiry)
    }

    def initialize(self):
        # There is only one trading operation per day
        # No need to sleep between iterations
        self.sleeptime = "1D"

        self.invalid_expiry_dates = []

    def on_trading_iteration(self):
        underlying_asset = self.parameters["underlying_asset"]
        fixed_income_symbol = self.parameters["fixed_income_symbol"]
        pct_call_out_of_money = self.parameters["pct_call_out_of_money"]
        pct_portfolio_in_calls = self.parameters["pct_portfolio_in_calls"]
        days_to_expiry = self.parameters["days_to_expiry"]
        days_before_expiry_to_sell = self.parameters["days_before_expiry_to_sell"]

        # Get all our positions
        positions = self.get_positions()

        # Get the amount of cash we have
        cash = self.get_cash()

        # Get the current value of our portfolio
        portfolio_value = self.get_portfolio_value()

        # Get the current price of the underlying asset
        underlying_price = self.get_last_price(underlying_asset)
        self.add_line(underlying_asset.symbol, underlying_price, "blue")

        # Get the price of the unleveraged asset
        fixed_income_price = self.get_last_price(fixed_income_symbol)

        # Add a lines to our chart for the fixed income symbol
        self.add_line(fixed_income_symbol, fixed_income_price, "red")

        # Check if we currently own any calls, if we don't, buy some
        own_calls = False
        for position in positions:
            if (
                position.asset.asset_type == Asset.AssetType.OPTION
                and position.asset.right == Asset.OptionRight.CALL
                and position.asset.symbol == underlying_asset.symbol
            ):
                own_calls = True

                # Check if the call option is about to expire
                dt = self.get_datetime().date()
                dt_to_expiry = position.asset.expiration - dt
                if days_before_expiry_to_sell is not None and dt_to_expiry < timedelta(
                    days=days_before_expiry_to_sell
                ):
                    # If the call option is about to expire, sell it
                    order = self.create_order(position.asset, position.quantity, "sell")
                    self.submit_order(order)

                    # Add a marker to our chart for when we sold
                    self.add_marker(
                        f"Sell {position.asset}",
                        symbol="triangle-down",
                        value=underlying_price,
                        color="red",
                    )

                    # Sleep for 5 seconds to make sure the order goes through
                    self.sleep(5)

                break

        # If we have extra cash, buy more of the fixed income ETF
        if cash > (fixed_income_price * 2):
            # If we don't own calls yet, subtract the amount of cash we will use to buy the calls
            if not own_calls:
                cash_for_fixed_income = cash - (
                    portfolio_value * pct_portfolio_in_calls
                )
            else:
                cash_for_fixed_income = cash

            # Buy more of the stock with the extra cash
            # Calculate the quantity of the asset we can buy
            quantity = cash_for_fixed_income // fixed_income_price

            # If we have enough cash to buy at least one share, buy
            if quantity >= 1:
                # Log that we are buying more of the fixed income ETF
                self.log_message(
                    f"Buying more of the fixed income ETF {fixed_income_symbol} because we have extra cash"
                )

                order = self.create_order(fixed_income_symbol, quantity, "buy")
                self.submit_order(order)

                # Add a marker to our chart for when we bought
                self.add_marker(
                    f"Buy {fixed_income_symbol}",
                    symbol="triangle-up",
                    value=fixed_income_price,
                    color="green",
                )
            else:
                # Log that we are not buying more of the fixed income ETF
                self.log_message(
                    f"Not buying more of the fixed income ETF {fixed_income_symbol} because we don't have extra cash"
                )
        else:
            # Log that we are not buying more of the fixed income ETF
            self.log_message(
                f"Not buying more of the fixed income ETF {fixed_income_symbol} because we don't have extra cash"
            )

        # If we don't own any calls, buy some
        if not own_calls:
            # Log that we are buying calls
            self.log_message(
                f"Buying calls on {underlying_asset} because we don't own any"
            )

            # Get the current value of our portfolio
            portfolio_value = self.get_portfolio_value()

            # Get how much cash we should call into the calls
            cash_in_calls = portfolio_value * pct_portfolio_in_calls

            # Get the current datetime
            dt = self.get_datetime()

            # Get the day around when we want to buy the calls
            expiry_date_idea = dt + timedelta(days=days_to_expiry)

            # Get the options expiration date
            expiry_date = self.get_option_expiration_after_date(expiry_date_idea)

            # If we have already tried to buy calls with this expiry date, skip
            if expiry_date in self.invalid_expiry_dates:
                return

            # Get the strike price
            strike = underlying_price * (1 + pct_call_out_of_money)

            # Round the strike price to the nearest 5 dollars
            strike = round(strike / 5) * 5

            # Create the call asset
            call_asset = Asset(
                underlying_asset.symbol,
                Asset.AssetType.OPTION,
                expiry_date,
                strike,
                Asset.OptionRight.CALL,
            )

            # Get the last price of the call
            call_price = self.get_last_price(call_asset)

            # We can't buy calls if we don't know the price (maybe it doesn't exist?)
            if call_price is None:
                # Add the expiry date to the list of invalid expiry dates
                self.invalid_expiry_dates.append(expiry_date)

                return

            # Calculate the quantity of calls we can buy (100 shares per contract)
            calls_quantity = cash_in_calls / call_price // 100

            # If we have enough cash to buy at least one call, buy
            if calls_quantity >= 1:
                # First, sell some of the stock to buy calls if needed

                # Check if we have enough cash to buy the calls
                if cash_in_calls > cash:
                    # If we don't have enough cash, sell some of the fixed income ETF

                    # Calculate the amount of extra cash we need
                    cash_needed = cash_in_calls - cash

                    # Get the quantity of the symbol to sell
                    symbol_sell_quantity = cash_needed // fixed_income_price

                    # If we should sell at least one share, sell
                    if symbol_sell_quantity >= 1:
                        order = self.create_order(
                            fixed_income_symbol, symbol_sell_quantity, "sell"
                        )
                        self.submit_order(order)

                        # Add a marker to our chart for when we sold
                        self.add_marker(
                            f"Sell {fixed_income_symbol}",
                            symbol="triangle-down",
                            value=fixed_income_price,
                            color="red",
                        )

                        # Sleep for 5 seconds to make sure the order goes through
                        self.sleep(5)

                # Second, buy the calls
                # Create the order
                calls_order = self.create_order(call_asset, calls_quantity, "buy")
                self.submit_order(calls_order)

                # Add a marker to our chart for when we bought
                self.add_marker(
                    f"Buy Call at {call_asset}",
                    symbol="triangle-up",
                    value=call_price,
                    color="green",
                    detail_text=f"Strike: {strike}",
                )
        else:
            # Log that we are not buying calls
            self.log_message(
                f"Not buying calls on {underlying_asset} because we already own some"
            )


if __name__ == "__main__":
    if not IS_BACKTESTING:
        ####
        # Run the strategy live
        ####

        trader = Trader()

        from credentials import TRADIER_CONFIG
        from lumibot.brokers import Tradier

        broker = Tradier(TRADIER_CONFIG)

        strategy = OptionsRollingCalls(
            broker=broker,
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL"),
            account_history_db_connection_str=os.environ.get(
                "ACCOUNT_HISTORY_DB_CONNECTION_STR"
            ),
        )
        trader.add_strategy(strategy)
        trader.run_all()

    else:
        ############################################
        # Backtest the strategy
        ############################################
        from credentials import POLYGON_CONFIG

        ####
        # Configuration Options
        ####

        backtesting_start = datetime(2020, 3, 1)
        backtesting_end = datetime(2024, 2, 8)
        trading_fee = TradingFee(percent_fee=0.001)  # 0.1% fee per trade

        # Set the name of the strategy based on the parameters
        params = OptionsRollingCalls.parameters
        strategy_name = f"Options Rolling Calls on {params['underlying_asset'].symbol} with {params['days_to_expiry']} DTE {params['pct_portfolio_in_calls']*100}% of portfolio in calls"

        ####
        # Start Backtesting
        ####

        OptionsRollingCalls.backtest(
            PolygonDataBacktesting,
            backtesting_start,
            backtesting_end,
            benchmark_asset="QQQ",
            buy_trading_fees=[trading_fee],
            sell_trading_fees=[trading_fee],
            polygon_api_key=POLYGON_CONFIG["API_KEY"],
            polygon_has_paid_subscription=True,
            name=strategy_name,
        )
