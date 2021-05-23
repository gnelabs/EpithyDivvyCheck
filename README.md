# EpithyDivvyCheck
Python script to perform lookups of stock options data in order to find dividend arbitrage opportunities.

There is no free lunch! Or is there? 
Get free money from divvies by finding underpriced put options. To be used by experienced traders only.

## Assumptions

* Ad-hoc lookups only.
* This tool searches for upcoming dividends roughly two weeks into the future.
* This tool acts as a screener to find periodic opportunities for trade.
* Calculations are pre-tax.
* Some data is cached in the local directory for speed at daily refresh intervals.

## Prerequisites

### IEX Cloud API key (paid service)

Sign up for IEX Cloud service in order to get an API key. This is a paid service required to collect information about upcoming dividends.
https://iexcloud.io/pricing/

Paste the key into iexcloud_key.txt file included with this script for it to work. This script will read that file from the same directory.
Example: sk_83d67a6d7659c978b565b89a8a75ad83

### Tradier API key

This tool relies on realtime options data delivered by the Tradier API. This is a paid brokerage account. If possible, it's simple
enough to fund a Tradier cash account with $50 in order to pay the annual inactivity fee and you'll get access to the API. The free
sandbox can be substituted in leui of a paid account by changing the URL variable, but data will be delayed 15 minutes.
https://developer.tradier.com/user/sign_up

Paste the key into tradier_bearer.txt file included with this script for it to work. This script will read that file from the same directory.
Should be in the JWT format example: Bearer asdf87aysdf87asydf87asydf87

### Libraries

Install these with pip (python3):

tqdm
columnar
lxml
bs4

## Usage

python .\divvycheck.py

## Sharp edges around dividend arbitrage.

* Special dividends (off-cycle) will cause OCC to initiate options contract changes which nullify the arbitrage. Contract changes are posted via the
OCC memo webpage and are retroactive. This is checked for in this tool by scanning the RSS feed, however it only goes back a week. Unusually high
yields should be manually checked.
* Dividend data is not perfect and varies between brokers and providers. Dividends, especially new starts of regular dividends should be manually
confirmed. Typically this is done through checking the earnings transcript or investor relations website for the security. For example, an annual
dividend is paid out quarterly but incorrectly reported as the full yearly amount.
* This tool assumes you'll be putting on long conversion trades to collect the arbitrage. In most cases, there is very little liquidity and you 
should be familiar with complex order types.
* ADR's that pay dividends are calculated using the spot exchange rate.
* Short calls can go in the money and be early exercised. If this happens, close the long put and move on.
* Long conversions are a trade put on with margin, despite being directionless. You can be margin called if the underlying share price increases
significantly.
* Trades are subject to pin risk. Trades should be closed manually if the underlying is near the strike on expiration.
* Stock splits or unusual corporate events may not be accounted for. 
* Trades are subject to slippage, which is not calculated in this tool.
* Avoid nonstandard options contract wherever possible.

## Known bugs (still investigating).

* Nonstandard underlyings with symbols in them like share classes (i.e. BRK/B) don't seem to show up properly despite attempting to strip these out.
These tend to show up as unrealistically generous yields because it's calculating off an unusual dividend not tracked with options.
* Nonstandard sized options contracts (not 100:1 ratio) also aren't being stripped out properly messing up calculations.
* Weird corporate events aren't being handled properly. I don't have code to handle these corner cases.

