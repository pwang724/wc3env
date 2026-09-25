# yyjson 0.13.0

Unmodified `src/yyjson.c`, `src/yyjson.h`, and `LICENSE` from
[upstream release 0.13.0](https://github.com/ibireme/yyjson/releases/tag/0.13.0).

SHA-256:

- `yyjson.c`: `d2d58ef0a3b2267862dc363832c4f3185fd375aea933d5d3419d1a940d6ece2e`
- `yyjson.h`: `ef803cda5c06b8962face6dfa39c3284b3bcf3e73f4d7317664690cc779679ca`
- `LICENSE`: `45e384d3d52c73cba3a64d6e6c25d47cd738cd8a55c30629e3201046eda62947`

`hook/build.bat` compiles the strict reader with a nesting limit of 64 and disables
the writer, incremental reader, file APIs, utilities, and nonstandard extensions.
`hook/json.h` handles request size/node budgets and checked conversion into native
argument types. Grammar, numeric parsing, Unicode decoding, and UTF-8 validation
belong to yyjson. To update, replace these three files from a pinned upstream
release and run the native regressions and live RPC contract tests.
