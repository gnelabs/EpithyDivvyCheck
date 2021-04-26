"""
IEX limits:
UPCOMING_DIVIDENDS = 5,950 per call
OPTIONS_EOD_DATES (stable/stock/IHY/options for list of dates) = 
STOCK_QUOTE (underlying quote) = 1 per call
FX_LATEST (currency converion list from tradeable divvies) = 500 per call
"""

import requests
import pickle
import logging
from pathlib import Path
from os import getcwd
from decimal import Decimal
#For progress bar in CLI. pip install tqdm
import tqdm

_LOGGER = logging.getLogger()
_LOGGER.setLevel(logging.INFO)


class MissingAPIKeyException(Exception):
    pass


class APIKeys(object):
    def get_key_from_file(self, filename: str, check_str: str, desc: str) -> str:
        """
        Verifies and grabs the API key.
        """
        my_file = Path(getcwd() + "/{0}".format(filename))
        windows_is_stupid_file = Path(getcwd() + "/{0}.txt".format(filename)) #Ugh, fucking windows.
        
        if not my_file.is_file():
            raise MissingAPIKeyException('Problem with {0} key. Unable to find file holding the key.'.format(desc))
        elif windows_is_stupid_file.is_file():
            raise MissingAPIKeyException('Problem with {0} key. Windows is stupid please double check the file name.'.format(desc))
        
        with open(my_file, 'r') as apikeyfile:
            api_key = apikeyfile.read()
            api_key.rstrip() #Remove newlines, carriage returns just in case.
            if check_str in api_key:
                return api_key
            else:
                raise MissingAPIKeyException('Problem with {0} key. Did not find correct format in key file.'.format(desc))
    
    def iexcloud_key(self):
        return self.get_key_from_file(
            filename = 'iexcloud_key.txt',
            check_str = 'sk_',
            desc = 'IEX Cloud'
        )
    
    def tradier_key(self):
        return self.get_key_from_file(
            filename = 'tradier_bearer.txt',
            check_str = 'Bearer',
            desc = 'Tradier'
        )


class DivvyData(object):
    def __init__(self):
        key_obj = APIKeys()
        self.PARAMS = {'token': key_obj.iexcloud_key(), 'format': 'json'}
        
    def initial_divvy_query(self) -> dict:
        """
        Initial bulk query to find upcoming divvies. Costs a huge credit.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying IEX for upcoming dividends.')
        #r = requests.get('https://cloud.iexapis.com/v1/stock/market/upcoming-dividends', params=self.PARAMS)
        #stuff = r.json(parse_float=Decimal)
        with open('4-18-divvies.pkl', 'rb') as f:
            stuff = pickle.load(f)
        
        raw_divvy_data = {}
        for item in stuff:
            raw_divvy_data[item['symbol']] = item
        
        return raw_divvy_data
    
    def grab_quotes(self, raw_divvy_data: dict) -> dict:
        """
        Grab quotes for all the symbols, punt otc symbols out because they won't have options.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying IEX for stock quotes.')
        quotes_dict = {}
        for item in tqdm.tqdm(raw_divvy_data.keys()):
            try:
                r = requests.get('https://cloud.iexapis.com/stable/stock/{0}/quote'.format(item), params=self.PARAMS)
                output = r.json(parse_float=Decimal)
                if 'OTC' not in output['primaryExchange']:
                    quotes_dict[item] = output
            except Exception as e:
                _LOGGER.error('Unable to query {0} for quote. {1}'.format(item, e))
        
        return quotes_dict
    
    def grab_options_expirations(self, quotes_dict: dict, raw_divvy_data: dict) -> dict:
        """
        Query exchange traded symbols for options chains. This returns a list of expirations
        and a 200 http status code if there are options. No options and it's a 404.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying IEX for options expirations.')
        divvies_with_options = {}
        for k, v in tqdm.tqdm(quotes_dict.items()):
            try:
                r = requests.get('https://cloud.iexapis.com/stable/stock/{0}/options'.format(k), params=self.PARAMS)
                if r.status_code == 404:
                    continue
                else:
                    divvies_with_options[k] = {
                        'quote': v,
                        'options_expirations': r.json(parse_float=Decimal),
                        'divvy': raw_divvy_data[k]
                    }
            except Exception as e:
                _LOGGER.error('Unable to query {0} for option expirations. {1}'.format(k, e))
        
        return divvies_with_options
    
    def currency_conversion(self, divvies_with_options: dict) -> dict:
        """
        Convert currencies. Divvies reported by IEX are in their local currency, but quotes
        for American traded symbols are in USD.
        """
        _LOGGER.info('Querying IEX for currency exchange rates.')
        currency_pairs = []
        currency_conversion = {}
        for k, v in divvies_with_options.items():
            if v['divvy']['currency'] != 'USD' and v['divvy']['amount'] != 0:
                try:
                    currency_pairs.append('USD{0}'.format(v['divvy']['currency']))
                    r = requests.get('https://cloud.iexapis.com/stable/fx/latest?symbols={0}'.format(','.join(currency_pairs)), params=self.PARAMS)
                    currency_output = r.json(parse_float=Decimal)
                    for item in currency_output:
                        currency_conversion[item['symbol'].split('USD')[1]] = item['rate']
                except Exception as e:
                    _LOGGER.error('Unable to query latest fx data. {1}'.format(e))
        
        return currency_conversion
    
    def update_yields(self, currency_conversion: dict, divvies_with_options: dict) -> dict:
        """
        Using currency conversion data, calculate one time dividend yield.
        """
        #To-do: needs to be fixed
        divvies_with_yield = {}
        for k, v in divvies_with_options.items():
            divvies_with_yield[k] = v
            if v['divvy']['currency'] == 'USD':
                divvies_with_yield[k]['div_yield'] = {
                    '%': round(Decimal(( v['divvy']['amount'] / v['quote']['latestPrice'] ) * 100 ), 2),
                    '$': v['divvy']['amount']
                }
            else:
                if v['divvy']['amount'] != 0:
                    currency_rate = currency_conversion[v['divvy']['currency']]
                    divvies_with_yield[k]['div_yield'] = {
                        '%': round(Decimal((( v['divvy']['amount'] / currency_rate ) / v['quote']['latestPrice'] ) * 100 ), 2),
                        '$': Decimal(v['divvy']['amount'])
                    }
        
        return divvies_with_yield

if __name__ == '__main__':
    pass
    # dd_obj = DivvyData()
    # raw_divvy_data = dd_obj.initial_divvy_query()
    # quotes_dict = dd_obj.grab_quotes(raw_divvy_data)
    # divvies_with_options = dd_obj.grab_options_expirations(quotes_dict=quotes_dict, raw_divvy_data=raw_divvy_data)
    # currency_conversion = dd_obj.currency_conversion(divvies_with_options)
    # divvies_with_yield = dd_obj.update_yields(currency_conversion=currency_conversion, divvies_with_options=divvies_with_options)