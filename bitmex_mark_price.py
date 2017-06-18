#!/usr/bin/python3
#Written by swapman
#Telegram: @swapman
#Twitter: @whalepool

from urllib.request import urlopen
import json
import dateutil.parser
from prettytable import PrettyTable
import time
import sys
from bitmex_ws import BitMEXWebsocket

# Initial setup parameters
DEBUG = False
SYMBOL = "XBTU17"
IMPACT_NOTIONAL = 10 * 1e8
ONE_YEAR = 60 * 60 * 24 * 365

#################################################################################
#   Computing BitMEX Mark Price                                             #
#
#       This is for non-perpetual futures contracts.
#
#       The main variables are:
#
#            - orderbook depth
#            - time until expiry
#            - index price (with its own separate formula, we take this as given)
#
#       First, the "impact mid" price is calculated. This is purely done using
#       orderbook data, and it represents the average of how deep on the bid and
#       ask side that 10 BTC (for XBT contracts) worth of order (unleveraged) will
#       get filled. The impact mid and impact ask price are the average fill price
#       the 10 BTC gets hit.
#
#       Next, the Fair Basis Rate is computed by taking the premium of the Impact
#   Mid (computed above) to the Index Price. We will assume here that the
#   index price is properly reported: https://www.bitmex.com/app/index/.BXBT30M
#
#   This premium is then discounted by the time to expiry for the contract,
#       so that the closer to expiration, the more it is weighted.
#
#       The basis rate is then used to compute the Fair Value from the index, and
#       discounted using the Time to Expiry so longer dated have the basis compounded
#
#       Finally the "fair price" is the sum of the index and this fair value.
#
########################################################################


def scrapeurl(url):
    '''Easy http fetch'''
    page = urlopen(url)
    data = page.read()
    decodedata = json.loads(data.decode())
    return decodedata


def makeXBTIndex():
    # Let's manually compute the BitMEX BTC/USD index for good measure
    # As of now it's 50/50 GDAX and Bitstamp

    urlb = "https://www.bitstamp.net/api/ticker"
    decodedatab = scrapeurl(urlb)
    stampprice = decodedatab['last']

    urlg = "https://api.gdax.com/products/BTC-USD/ticker"
    decodedatag = scrapeurl(urlg)
    gdaxprice = decodedatag['price']

    return (float(stampprice)+float(gdaxprice)) / 2


def getInstrument(symbol):
    return scrapeurl("https://www.bitmex.com/api/v1/instrument?symbol="+symbol)[0]


def pluck(dict, *args):
    '''Returns destructurable keys from dict'''
    return (dict[arg] for arg in args)


def value(multiplier, price, qty):
    '''Returns the value of a book level, in satoshis'''
    contVal = abs(multiplier * price if multiplier > 0 else multiplier / price)
    return round(qty * contVal)


def calculateImpactSide(instrument, book, side):
    '''
    The way we compute Impact Prices is by going into either side
    of the book for 10 BTC worth of order values, and take the average
    price filled.
    '''
    notional = 0
    impactPrice = 0

    for orderBookItem in book:
        size = orderBookItem[side + 'Size']
        price = orderBookItem[side + 'Price']

        # No more book levels; will create a situation where `hasLiquidity: false`
        if size is None or price is None:
            break

        # No more to do
        if notional >= IMPACT_NOTIONAL:
            break

        # Calculate value. Contract may be inverse, linear, or quanto.
        levelValue = value(instrument['multiplier'], price, size)

        # Calculate an average price, up to the IMPACT_NOTIONAL.
        remainingValue = min(levelValue, IMPACT_NOTIONAL - notional)
        notional += remainingValue
        impactPrice += (remainingValue / IMPACT_NOTIONAL) * price
        if DEBUG:
            print('side: %s, levelValue: %.2f, price: %.2f, size: %d, remainingValue: %.2f, notional: %.2f, impactPrice: %.2f' %
                  (side, levelValue / 1e8, price, size, remainingValue / 1e8, notional / 1e8, impactPrice))

    return impactPrice


def getImpactPrices(instrument):
    # Grab the Orderbook so we can grab the depth for bids and asks for impact prices
    symbol = instrument['symbol']
    fullBook = scrapeurl("https://www.bitmex.com/api/v1/orderBook?symbol="+symbol+"&depth=200")

    impactBid = calculateImpactSide(instrument, fullBook, 'bid')
    impactAsk = calculateImpactSide(instrument, fullBook, 'ask')

    # The % Fair Basis is updated each minute but only if the difference between the Impact Ask Price and
    # Impact Bid Price is less than the maintenance margin of the futures contract.
    # After it has been updated the Fair Price will be equal to the Impact Mid Price,
    # and then the Fair Price will float with regard to the Index Price and the time-to-expiry
    # decay on the contract until the next update.
    if abs(impactBid - impactAsk) > (instrument['midPrice'] / instrument['maintMargin']):
        print('Note: impactBid and impactAsk are farther apart than 1x maintMargin; hasLiquidity would be ' +
              'false, and the instrument\'s fair basis will not update until the prices converge again.')

    impactMid = (impactBid + impactAsk) / 2
    return (impactBid, impactMid, impactAsk)


def fullCalculation(instrument):

    # Calculate the time to expiry by grabbing the expiry TS
    expiryDate = dateutil.parser.parse(instrument['expiry'])

    # Get seconds until expiry
    timeUntilExpirySec = round(expiryDate.timestamp() - time.time())
    timeUntilExpiryYears = timeUntilExpirySec / ONE_YEAR

    print("Time to Expiry: %.2f Days" % (timeUntilExpirySec / (60 * 60 * 24)))

    # Impact Mid computation matches up close (but not perfect) with BitMEX's posted
    impactBid, impactMid, impactAsk = getImpactPrices(instrument)

    # Fair price calculation
    indexPrice = makeXBTIndex()

    # From the BitMEX site https://www.bitmex.com/app/fairPriceMarking :

    # % Fair Basis = (Impact Mid Price / Index Price - 1) / (Time To Expiry / 365)
    # Fair Value   = Index Price * % Fair Basis * (Time to Expiry / 365)
    # Fair Price   = Index Price + Fair Value

    fairBasisRate = (impactMid / indexPrice-1) / timeUntilExpiryYears
    fairBasis = indexPrice * fairBasisRate * timeUntilExpiryYears
    fairPrice = indexPrice + fairBasis

    return {
        'indicativeSettlePrice': indexPrice,
        'impactBidPrice': impactBid,
        'impactAskPrice': impactAsk,
        'impactMidPrice': impactMid,
        'fairBasisRate': fairBasisRate,
        'fairBasis': fairBasis,
        'fairPrice': fairPrice
    }


def printResults(instrument, calcResult):

    table = PrettyTable(['Key', 'BitMEX', 'Computed', 'Difference'])
    table.float_format = ".2"
    table.align = 'r'
    rows = [
        # Label, Key
        ['Index Price', 'indicativeSettlePrice'],
        ['Impact Bid', 'impactBidPrice'],
        ['Impact Ask', 'impactAskPrice'],
        ['Impact Mid', 'impactMidPrice'],
        ['%% Fair Basis Rate', 'fairBasisRate', lambda x: "%.2f%%" % (x * 100)],
        ['Fair Basis', 'fairBasis'],
        ['Fair Price', 'fairPrice']
    ]
    for row in rows:
        label, key = row[:2]
        # Formatter
        fn = row[2] if len(row) == 3 else lambda x: x
        table.add_row([label, fn(instrument[key]), fn(calcResult[key]), fn(calcResult[key] - instrument[key])])

    print(table)

#######################################################################


def main():
    websocket = BitMEXWebsocket()
    websocket.connect(symbol=SYMBOL)
    instrument = websocket.get_instrument(SYMBOL)
    calcResult = fullCalculation(instrument)
    print('Initial Calculation:')
    printResults(instrument, calcResult)
    print('Note that this calculation\'s fairBasisRate was not calculated at the same time as the trading engine, ' +
          'which will cause some divergence.')
    print('For more accuracy, waiting until next fairPrice update.')
    lastFairBasisRate = instrument['fairBasisRate']
    iters = 0
    while True:
        time.sleep(0.1)
        sys.stdout.write("\rWaiting" + (((iters % 5) + 1) * '.'))
        sys.stdout.flush()
        iters += 1
        instrument = websocket.get_instrument(SYMBOL)
        if instrument['fairBasisRate'] != lastFairBasisRate:
            print('Caught change of fairBasisRate from %.2f to %.2f. Recalculating...' %
                  (lastFairBasisRate, instrument['fairBasisRate']))
            calcResult = fullCalculation(instrument)
            printResults(instrument, calcResult)
            break


# Init
if __name__ == "__main__":
    main()
