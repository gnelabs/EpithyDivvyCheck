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
from typing import Callable, Generator
from os import getcwd, path
from time import sleep, strftime
from datetime import datetime, date
from decimal import Decimal
from collections import OrderedDict, defaultdict
#For progress bar in CLI. pip install tqdm
import tqdm
#Pretty columns for CLI display. pip install columnar
from columnar import columnar
#Parsing rss xml feed. pip install lxml, pip install bs4
from bs4 import BeautifulSoup as bbb

_LOGGER = logging.getLogger()
_LOGGER.setLevel(logging.INFO)

# constants
OPTION_CONTRACT_COST = 1 #Assuming a one-lot contract, $1 minimum. Conservative estimate.
CONTRACT_ACTIONS_PER_COLLAR = 4 #Number of contract actions required to manage a collar without pin risk.
OPTION_CONTRACT_SIZE = 100


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


class CachedData(object):
    def __init__(self):
        self.CACHED_DIVVIES_FILENAME = '{0}-divvies.pkl'.format(strftime('%d-%m-%y'))
    
    def load(self) -> dict:
        """
        Load cached data so Dividend search, initial quotes, and expirations don't
        have to be loaded every single time since this information rarely changes.
        """
        filepath = path.join(getcwd(), self.CACHED_DIVVIES_FILENAME)
        if path.exists(filepath):
            with open(filepath, 'rb') as f:
                cache = pickle.load(f)
        else:
            cache = {}
        
        return cache
    
    def save(self, divvies_with_exp: dict) -> None:
        """
        Save cached data to hard disk to be reused later.
        """
        filepath = path.join(getcwd(), self.CACHED_DIVVIES_FILENAME)
        with open(filepath, 'wb') as f:
            pickle.dump(divvies_with_exp, f)
        
        return


class DivvyData(object):
    def __init__(self, api_key: str):
        self.api_key = api_key
        
    def initial_divvy_query(self) -> dict:
        """
        Initial bulk query to find upcoming divvies. Costs a huge credit.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying IEX for upcoming dividends.')
        
        try:
            params = {'token': self.api_key, 'format': 'json'}
            r = requests.get('https://cloud.iexapis.com/v1/stock/market/upcoming-dividends', params=params)
            stuff = r.json(parse_float=Decimal)
        except Exception as e:
            _LOGGER.error('Unable to query for upcoming dividends. {0}'.format(e))
        
        raw_divvy_data = {}
        for item in stuff:
            #In case stock has different classes, remove. I.e. 'CWEN.A' -> 'CWEN' This just causes problems later.
            symbol = item['symbol'].split('.')[0]
            raw_divvy_data[item['symbol']] = item
        
        return raw_divvy_data
    
    def _chunks(self, lst: list, n: int) -> Generator[str, list, list]:
        """
        Quick list chunker stolen from stack overflow. Credit Ned Batchelder
        """
        for i in range(0, len(lst), n):
            yield lst[i:i + n]
    
    def _punt_otc(self, quotes_output: list) -> dict:
        """
        Punt out symbols on OTC, they won't have an option chain.
        """
        quotes_dict = {}
        for item in quotes_output:
            for k, v in item.items():
                try:
                    if 'OTC' not in v['quote']['primaryExchange']:
                        quotes_dict[k] = v['quote']
                except Exception as e:
                    _LOGGER.error('Unable to filter quote for {0}. {1}'.format(k, e))
        
        return quotes_dict
        
    def grab_quotes(self, raw_divvy_data: dict) -> dict:
        """
        Grab quotes for all the symbols, punt otc symbols out because they won't have options.
        IEX returns stock quotes with a 15-minute delay. Used for general calibrating the DIV
        yield since its unlikely divvy stocks are making crazy moves. Options calculations use
        the realtime quotes provided by Tradier.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying IEX for stock quotes.')
        
        symbols_list = list(raw_divvy_data.keys())
        quotes_output = []
        
        #Batch requests by 100, the iex quote limit.
        for item in tqdm.tqdm(self._chunks(lst=symbols_list, n=100)):
            params = {
                'token': self.api_key,
                'format': 'json',
                'symbols': ','.join(item),
                'types': 'quote'
            }
            
            try:
                r = requests.get('https://cloud.iexapis.com/stable/stock/market/batch', params=params)
                quotes_output.append(r.json(parse_float=Decimal))
            except Exception as e:
                _LOGGER.error('Unable to query {0} for quotes. {1}'.format(item, e))
        
        return self._punt_otc(quotes_output)
    
    def currency_conversion(self, divvies_with_options: dict) -> dict:
        """
        Convert currencies. Divvies reported by IEX are in their local currency, but quotes
        for American traded symbols are in USD.
        
        Returns a dict like {'CAD': Decimal('1.21294')}
        """
        _LOGGER.info('Querying IEX for currency exchange rates.')
        currency_pairs = []
        currency_conversion = defaultdict(dict)
        divvies_with_yield = defaultdict(dict)
        
        #Initial filtering for bad/no data from IEX.
        for k, v in divvies_with_options.items():
            currency_type = v['divvy']['currency']
            currency_amount = v['divvy']['amount']
            
            if currency_type == 'USD':
                if currency_amount != 0:
                    divvies_with_yield[k] = v
                    divvies_with_yield[k]['div_yield'] = {
                        '%': round(Decimal(( Decimal(currency_amount) / Decimal(v['quote']['latestPrice']) ) * 100 ), 2),
                        '$': currency_amount
                    }
            else:
                if currency_amount != 0:
                    if currency_type:
                        currency_pairs.append('USD{0}'.format(currency_type))
                        currency_conversion[k] = currency_type
            
            if currency_amount == 0:
                continue
        
        #Once all non USD currency pairs are determined, query for the latest exchange rates.
        if currency_pairs:
            try:
                params = {
                    'token': self.api_key,
                    'format': 'json',
                    'symbols': ','.join(set(currency_pairs))
                }
                r = requests.get('https://cloud.iexapis.com/stable/fx/latest', params=params)
                currency_output = r.json(parse_float=Decimal)
                if r.status_code != 200:
                    raise Exception('Unable to query latest fx data.Status code: {0}. Input data: {1} Output data: {2}'.format(r.status_code, currency_pairs, currency_output))
            except Exception as e:
                raise Exception('Unable to query latest fx data.Status code: {0}. Input data: {1} Output data: {2} Error: {3}'.format(r.status_code, currency_pairs, currency_output, e))
            
            #Fill in the yield using the exchange rate.
            for k, v in currency_conversion.items():
                for item in currency_output:
                    try:
                        currency_symbol = item['symbol'].replace('USD', '')
                        if currency_symbol == v:
                            divvies_with_yield[k] = divvies_with_options[k]
                            divvies_with_yield[k]['div_yield'] = {
                                '%': round(Decimal((( Decimal(divvies_with_options[k]['divvy']['amount']) / Decimal(item['rate']) ) / Decimal(divvies_with_options[k]['quote']['latestPrice']) ) * 100 ), 2),
                                '$': Decimal(divvies_with_options[k]['divvy']['amount'])
                            }
                    except Exception as e:
                        raise Exception('Unable to query convert currency on {0}. Data: {1} Error: {2}'.format(k, item, e))
        
        return divvies_with_yield


class OptionsData(object):
    def __init__(self, api_key: str):
        self.headers = {"Accept": "application/json", "Authorization": api_key}
        self.api = 'https://api.tradier.com{0}'
        self.ratelimit_available = 120 #I think they say 120 calls/min?
    
    def throttle(self):
        """
        Function to throttle requests when rate limit available starts getting low.
        
        Easier than handling rate limits.
        """
        if self.ratelimit_available < 20:
            sleep(1)
        
        return
    
    def quotes(self, symbol: str) -> dict:
        """
        Using the inputted quote, grab realtime prices. Used to calculate spreads.
        """
        url = self.api.format('/v1/markets/quotes')
        params = {'symbols': symbol}
        try:
            r = requests.get(url, headers=self.headers, params=params)
            self.ratelimit_available = int(r.headers['X-Ratelimit-Available'])
            data = r.json()['quotes']['quote']
            return r.json()['quotes']['quote']
        except Exception as e:
            raise Exception('Problem querying stock quotes. Status code: {0} Symbol: {1} Error: {2}'.format(r.status_code, symbol, e))
    
    def expirations(self, symbol: str) -> list:
        """
        Need to gather available options expirations before querying the chains.
        """
        url = self.api.format('/v1/markets/options/expirations')
        params = {'symbol': symbol, 'includeAllRoots': 'true', 'strikes': 'false'}
        try:
            r = requests.get(url, headers=self.headers, params=params)
            self.ratelimit_available = int(r.headers['X-Ratelimit-Available'])
            
            response = r.json()['expirations']
            
            if response:
                return response['date']
            else:
                return []
        except Exception as e:
            raise Exception('Problem querying expirations for {0}. Status code: {1} Error: {2}'.format(symbol , r.status_code, e))
    
    def options_chain(self, expiration_date: str, symbol: str) -> list:
        """
        With the option expiration grab the options chain data.
        """
        url = self.api.format('/v1/markets/options/chains')
        params = {'symbol': symbol, 'expiration': expiration_date, 'greeks': 'true'}
        try:
            r = requests.get(url, headers=self.headers, params=params)
            self.ratelimit_available = int(r.headers['X-Ratelimit-Available'])
            options_chain_data = r.json()['options']
            
            #Occasionally IEX will report ghost options expirations.
            if options_chain_data:
                return options_chain_data['option']
            else:
                return []
        except Exception as e:
            raise Exception('Problem querying options chain for {0}. Status code: {1} Error: {2}'.format(symbol , r.status_code, e))
    
    def grab_options_expirations(self, quotes_dict: dict, raw_divvy_data: dict) -> dict:
        """
        Query exchange traded symbols for options expirations. This filters out symbols.
        Using Tradier data since it's more reliable than IEX for options.
        
        Returns a dictionary with tickers as keys like {'PSTX': {'data...': 0}}
        """
        _LOGGER.info('Querying Tradier for options expirations.')
        divvies_with_options = {}
        
        for k, v in tqdm.tqdm(quotes_dict.items()):
            options_expiration_list = self.expirations(k)
            
            if options_expiration_list:
                divvies_with_options[k] = {
                    'quote': v,
                    'options_expirations': options_expiration_list,
                    'divvy': raw_divvy_data[k]
                }
        
        return divvies_with_options
    
    def grab_all_options_data(self, divvies_with_exp: dict) -> dict:
        """
        Grab options data for each symbol to compile a complete picture.
        
        Options chain and realtime stock quote grabbed at the same time to keep spread data fresh.
        """
        _LOGGER.info('Grabbing current stock price and options expirations for {0} symbols.'.format(len(divvies_with_exp)))
        
        all_data = {}
        
        for k, v in tqdm.tqdm(divvies_with_exp.items()):
            expirations = v['options_expirations']
            
            all_data[k] = v
            
            options_data = {}
            for expiration in expirations:
                self.throttle()
                
                options_chain = self.options_chain(expiration_date=expiration, symbol=k)
                
                #Kick out nonstandard sized contracts.
                options_chain_standard_contract_size = []
                for item in options_chain:
                    if item['contract_size'] == 100:
                        options_chain_standard_contract_size.append(item)
                
                options_data[expiration] = options_chain_standard_contract_size
            
            all_data[k]['options_data'] = options_data
            self.throttle()
            
            all_data[k]['realtime_quote'] = self.quotes(k)
        
        return all_data
    
    def get_occ_memos(self) -> list:
        """
        Looks at the OCC memo rss feed to see if there are any recent memos.
        This prevents putting on an arb with adjusted contract terms.
        """
        _LOGGER.info('Grabbing OCC memos rss feed.')
        memo_titles = []
        try:
            r = requests.get('https://infomemo.theocc.com/infomemo-rss')
            soup = bbb(r.content, features='xml')
            memo_titles = []
            for rssitem in soup.findAll('item'):
                memo_titles.append(rssitem.find('description').text)
        except Exception as e:
            raise Exception('Unable to get OCC memos, {0}'.format(e))
        
        return memo_titles


class Calculations(object):
    def __init__(self):
        pass
    
    def find_arbs(self, data: dict, occmemos: list) -> dict:
        """
        Filter through the data to find underpriced puts.
        """
        _LOGGER.info('Filtering through data to find arbs.')
        profitable_trades = defaultdict(dict)
        
        options_fees_paid = OPTION_CONTRACT_COST * CONTRACT_ACTIONS_PER_COLLAR
        
        
        for k, v in data.items():
            current_underlying_ask = round(Decimal(v['realtime_quote']['ask']), 2)
            ex_dividend_date = datetime.strptime(v['divvy']['exDate'], "%Y-%m-%d")
            record_date = datetime.strptime(v['divvy']['recordDate'], "%Y-%m-%d")
            
            #Verify today isn't already the ex-div.
            if ex_dividend_date.date() == date.today():
                continue
            
            #Verify ticker isn't in an OCC memo.
            ticker_in_occ_memo = False
            for memo in occmemos:
                if 'Symbol: {0}'.format(k.upper()) in memo:
                    ticker_in_occ_memo = True
            if ticker_in_occ_memo:
                continue
            
            #Figure out the next options expiration date after the ex-div.
            #Sometimes arbs can be put on later expirations, but for now focusing
            #on just the closest one.
            expiration_after_record = None
            for expiration_date in sorted(v['options_expirations']):
                if datetime.strptime(expiration_date, "%Y-%m-%d") >= record_date:
                    expiration_after_record = expiration_date
                    #Ordered by earliest first, break once its found.
                    break
            
            #Iterate through the chain to collect data. First pass.
            #This is needed to combine put and call data to a single strike for symmetric collars.
            options_bid_ask_prices = defaultdict(dict)
            for option in v['options_data'][expiration_after_record]:
                strike = round(Decimal(option['strike']), 2)
                option_type = option['option_type']
                try:
                    bid = Decimal(option['bid'])
                    ask = Decimal(option['ask'])
                except TypeError:
                    #No bids shows as a nonetype, return zero.
                    bid = Decimal(0)
                    ask = Decimal(0)
                
                #Throw out strikes below the underlying. We only want ITM puts.
                if strike < current_underlying_ask:
                    continue
                
                #Two decimal places to keep it simple.
                if option_type == 'put':
                    options_bid_ask_prices[strike]['put_ask'] = round(ask, 2)
                elif option_type == 'call':
                    options_bid_ask_prices[strike]['call_bid'] = round(bid, 2)
            
            #Second (third?) pass, perform calculations.
            for option in v['options_data'][expiration_after_record]:
                strike = round(Decimal(option['strike']), 2)
                option_type = option['option_type']
                
                #Skip calls, just focus on puts so this is only done once per strike.
                #And skip strikes that aren't in options_bid_ask_prices meaning they're not ITM puts.
                if option_type == 'call':
                    continue
                elif strike not in options_bid_ask_prices.keys():
                    continue
                
                #Face value bid & ask are taken instead of calculating the mid.
                #Generally, if the trade is profitable at the bid and ask, then a mid fill should account for slippage.
                synthetic_short_debit_price = ( options_bid_ask_prices[strike]['put_ask'] - options_bid_ask_prices[strike]['call_bid'] )
                put_intrinsic_value = ( strike - current_underlying_ask )
                dividend_amount_usd = v['div_yield']['$']
                dividend_yield_pcnt =  v['div_yield']['%']
                put_volume = option['volume']
                ex_date = v['divvy']['exDate']
                expiration = option['expiration_date'] + ' ({0})'.format(( datetime.strptime( expiration_after_record, "%Y-%m-%d" )-datetime.now() ).days )
                
                profit_per_longconv = round(((( dividend_amount_usd - ( synthetic_short_debit_price - put_intrinsic_value )) * OPTION_CONTRACT_SIZE) - options_fees_paid), 2)
                
                #Fill in data for profitable trades to display.
                if profit_per_longconv > 0:
                    #Only overwrite if yield is higher. This way the optimal strike is chosen.
                    try:
                        yield_for_previous_strike = profitable_trades[k]['div_yield']
                    except KeyError:
                        yield_for_previous_strike = 0
                    
                    if yield_for_previous_strike < dividend_yield_pcnt:
                        profitable_trades[k]['strike'] = '${0}'.format(strike)
                        profitable_trades[k]['underlying'] = '${0}'.format(current_underlying_ask)
                        profitable_trades[k]['div_amount'] = '${0}'.format(dividend_amount_usd)
                        profitable_trades[k]['profit_on_longconv'] = '${0}'.format(profit_per_longconv)
                        profitable_trades[k]['div_yield'] = dividend_yield_pcnt
                        profitable_trades[k]['ex_date'] = ex_date
                        profitable_trades[k]['put_volume'] = put_volume
                        profitable_trades[k]['expiration'] = expiration
        
        return profitable_trades


if __name__ == '__main__':
    #Grab api keys.
    key_obj = APIKeys()
    tradier_api_key = key_obj.tradier_key()
    iex_key = key_obj.iexcloud_key()
    
    #Initialize objects.
    dd_obj = DivvyData(iex_key)
    opts_obj = OptionsData(tradier_api_key)
    calcs_obj = Calculations()
    cache_obj = CachedData()
    
    #Check for cache first
    divvies_with_exp = cache_obj.load()
    if not divvies_with_exp:
        #Grab dividend and quotes. This gets the initial list of future dividends
        #and filters by lit exchanges.
        raw_divvy_data = dd_obj.initial_divvy_query()
        quotes_dict = dd_obj.grab_quotes(raw_divvy_data)
        
        #Grab options expirations. This further filters by removing stocks with
        #no options.
        divvies_with_exp = opts_obj.grab_options_expirations(quotes_dict=quotes_dict, raw_divvy_data=raw_divvy_data)
        
        #Cache.
        cache_obj.save(divvies_with_exp)
    
    #Filtered down to symbols with options expirations, grab the chain data.
    divvies_with_options = opts_obj.grab_all_options_data(divvies_with_exp)
    
    #Convert non USD currency divvies into USD.
    divvies_with_yield = dd_obj.currency_conversion(divvies_with_options)
    
    #Grab the RSS feed for OCC memos to filter out special divvies which end up as adjusted contracts and no arb.
    occmemos = opts_obj.get_occ_memos()
    
    #Run calculations with data collected to find arbs.
    current_arbs = calcs_obj.find_arbs(data = divvies_with_yield, occmemos = occmemos)
    
    headers_sym = ['ticker']
    free_money = []
    
    #Fill in headers.
    for v in current_arbs.values():
        for k in v.keys():
            headers_sym.append(k)
        break
    
    #Fill in a list to display, ordered by highest div_yield.
    for item in sorted(current_arbs.items(), key=lambda x: x[1]['div_yield'], reverse=True):
        _temp_list = []
        _temp_list.append(item[0])
        for k, v in item[1].items():
            #Add % after sorting. Have to do after otherwise it messes up sorting.
            if k == 'div_yield':
                _temp_list.append('{0}%'.format(v))
            else:
                _temp_list.append(v)
        free_money.append(_temp_list)
    
    print('Profitable long conversion arbitrage trades using dividends:')
    print(columnar(free_money, headers_sym, no_borders=True, patterns=[]))
    