# Autonomous Trading Bot

Runs hourly via GitHub Actions. Trades crypto on Alpaca Paper Trading.

## Setup

1. Fork this repo
2. Add GitHub Secrets:
   - `APCA_API_KEY_ID` - Your Alpaca API key
   - `APCA_API_SECRET_KEY` - Your Alpaca secret
3. Enable GitHub Actions
4. Enable GitHub Pages (Settings > Pages > Source: main /docs)

## Dashboard

Once GitHub Pages is enabled, visit:
`https://<username>.github.io/<repo-name>/`

## Coins

18 coins scanned: BTC, SOL, ETH, DOGE, ADA, AVAX, LINK, DOT, LDO, ARB, XRP, LTC, BCH, UNI, AAVE, CRV, GRT, FIL

## Manual Run

Go to Actions > Trading Bot > Run workflow
