{ pkgs }: {
  deps = [
    pkgs.openssl
    pkgs.glibcLocales
    pkgs.glibc
    pkgs.postgresql
  ];
}