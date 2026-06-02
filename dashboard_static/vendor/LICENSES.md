# Vendored frontend libraries

These files are shipped verbatim from upstream so the dashboard works on an
air-gapped LAN. They are unmodified.

## react.production.min.js — 18.3.1

- Source: https://unpkg.com/react@18.3.1/umd/react.production.min.js
- License: MIT — Copyright (c) Meta Platforms, Inc. and affiliates.
- SHA-256: `d949f1c3687aedadcedac85261865f29b17cd273997e7f6b2bfc53b2f9d4c4dd`

## react-dom.production.min.js — 18.3.1

- Source: https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js
- License: MIT — Copyright (c) Meta Platforms, Inc. and affiliates.
- SHA-256: `35f4f974f4b2bcd44da73963347f8952e341f83909e4498227d4e26b98f66f0d`

## babel.min.js — @babel/standalone 7.29.0

- Source: https://unpkg.com/@babel/standalone@7.29.0/babel.min.js
- License: MIT — Copyright (c) 2014-present Sebastian McKenzie and other contributors.
- SHA-256: `2623a9e22809915ce789b4461154e277ddce520d5a4320c14d44332a5d0dcea0`

## Refresh procedure

```bash
cd dashboard_static/vendor
curl -sSL -o react.production.min.js     https://unpkg.com/react@18.3.1/umd/react.production.min.js
curl -sSL -o react-dom.production.min.js https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js
curl -sSL -o babel.min.js                https://unpkg.com/@babel/standalone@7.29.0/babel.min.js
shasum -a 256 *.js              # update this file if hashes change
```
