# EpithyDivvyCheck
Python script to perform lookups of stock options data in order to find dividend arbitrage opportunities.

Get free money from divvies by finding underpriced put options. To be used by experienced traders only.

## Assumptions

* Ad-hoc lookups only.
* This tool acts as a screener to find periodic opportunities for trade.
* Calculations are pre-tax.

## Prerequisites

### IEX Cloud API key (paid service)

Sign up for IEX Cloud service in order to get an API key. This is a paid service required to collect information about upcoming dividends.
https://iexcloud.io/pricing/

Paste the key into iexcloud_key.txt file included with this script for it to work. This script will read that file from the same directory.
Example: sk_83d67a6d7659c978b565b89a8a75ad83

### Tradier sandbox API key

Create a Tradier sandbox developer account to get an API key. This is free and will allow you to get realtime options data required for this script.
https://developer.tradier.com/user/sign_up

Paste the key into tradier_bearer.txt file included with this script for it to work. This script will read that file from the same directory.
Should be in the JWT format example: Bearer asdf87aysdf87asydf87asydf87

### Libraries

Install these with pip (python3):

tqdm

## Usage

python .\iexcloud1.py

## Sharp edges around dividend carry.

* To be filled out.

