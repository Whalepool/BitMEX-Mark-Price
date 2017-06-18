#!/usr/bin/python3
#Written by swapman
#Telegram: @swapman
#Twitter: @whalepool

from urllib.request import urlopen
import json
import dateutil.parser
from prettytable import PrettyTable

# Initial setup parameters

SYMBOL = "XBTU17"
IMPACT_NOTIONAL = 10

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

# We're going to be scraping a few api endpoints so easier to use this little f

def scrapeurl(url):
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


def getImpactPrices(symbol):
    # Grab the Orderbook so we can grab the depth for bids and asks for impact prices

    decodedata = scrapeurl("https://www.bitmex.com/api/v1/orderBook?symbol="+symbol+"&depth=200")

    # Set base values for the amount of bids & asks left until hittign IMPACT_NOTIONAL in BTC value
    bidsleft = asksleft = 0

    # Set the base value for the impact bid and asks prices
    impactBid = impactAsk = 0

    ######################################################################
    # The way we compute Impact Prices is by going into either side      #
    # of the book for 10 BTC worth of order values, and take the average #
    # price filled.                                                      #
    ######################################################################

    # To do this, we loop over every orderbook level from BitMEX for the instrument
    for orderBookItem in decodedata:
        level, bidSize, bidPrice = pluck(orderBookItem, 'level', 'bidSize', 'bidPrice')

        # Code for Impact bid

        # make sure the parms are both not null
        if bidSize is not None and bidPrice is not None:
            # What is most important with inverse futures is notional BTC value, so we compute the BTC order value
            bidbtc = bidSize/bidPrice

        # Check that the amount of bids left until IMPACT_NOTIONAL is below the IMPACT_NOTIONAL
        if bidsleft < IMPACT_NOTIONAL:
            # Add to the running count of how many BTC is left in comparison to IMPACT_NOTIONAL
            bidsleft = bidsleft+bidbtc

            # The average price is the bid price at each level weighted by the level's BTC size as proportion of IMPACT_NOTIONAL
            impactBid = impactBid+(bidbtc/IMPACT_NOTIONAL)*bidPrice

            # If the current order level pushes above IMPACT_NOTIONAL count, then we re-compute the impactBid
            if bidsleft > IMPACT_NOTIONAL:
                impactBid = impactBid-(bidbtc/IMPACT_NOTIONAL)*bidPrice

                # Wind back to prior level
                priorlastbid = bidsleft-bidbtc

                # We only want to have the last amount up to IMPACT_NOTIONAL, not the part above

                lastbid = IMPACT_NOTIONAL-priorlastbid

                # This will be the impact bid instead
                impactBid = impactBid+bidPrice*(lastbid/IMPACT_NOTIONAL)

        # Code for Impact ask
        # See comments above, same exact format
        askSize, askPrice = pluck(orderBookItem, 'askSize', 'askPrice')

        if askSize is not None and askPrice is not None:
            askbtc = askSize/askPrice

        if asksleft < IMPACT_NOTIONAL:
            asksleft = asksleft+askbtc

            impactAsk = impactAsk+(askbtc/IMPACT_NOTIONAL)*askPrice

            if asksleft > IMPACT_NOTIONAL:
                impactAsk = impactAsk-(askbtc/IMPACT_NOTIONAL)*askPrice
                priorlastask = asksleft-askbtc
                lastask = IMPACT_NOTIONAL-priorlastask
                impactAsk = impactAsk+askPrice*(lastask/IMPACT_NOTIONAL)

    # Calc impact mid
    impactMid = (impactAsk+impactBid)/2

    return (impactBid, impactMid, impactAsk)

#######################################################################
# First let's grab some stats about the symbol instrument

def main():

    instrument = getInstrument(SYMBOL)

    # Calculate the time to expiry by grabbing the expiry TS format and the current timestamp
    expiryts = instrument['expiry']
    timestampts = instrument['timestamp']

    # Have to get into integer format from BitMEX's stamp format
    expiryd = dateutil.parser.parse(expiryts)
    timestampd = dateutil.parser.parse(timestampts)

    # Round down to the seconds
    timestampsec = round(timestampd.timestamp())
    expirysec = round(expiryd.timestamp())

    tteseconds = expirysec-timestampsec

    # Finally, put it into days
    ttedays = tteseconds/60/60/24  # This shows the value of days in a fraction down to the second. Maybe BitMEX rounds up or down?
    TTE = ttedays  # This will be used for the fair basis calculations at the bottom of script
    print("Time to Expiry: %.2f Days" % TTE)

    # Impact Mid computation matches up close (but not perfect) with BitMEX's posted
    impactBid, impactMid, impactAsk = getImpactPrices(SYMBOL)

    # Fair price calculation
    indexPrice = makeXBTIndex()

    # From the BitMEX site https://www.bitmex.com/app/fairPriceMarking :

    # % Fair Basis = (Impact Mid Price / Index Price - 1) / (Time To Expiry / 365)
    # Fair Value   = Index Price * % Fair Basis * (Time to Expiry / 365)
    # Fair Price   = Index Price + Fair Value

    fairBasis = (impactMid / indexPrice-1) / (TTE / 365)
    fairValue = indexPrice * fairBasis * (TTE / 365)
    fairPrice = indexPrice + fairValue

    def makeResults(indexPrice, impactBid, impactAsk, impactMid, fairBasisRate, fairBasis, markPrice):
        return [
            'Index Price: %.2f' % indexPrice,
            'Impact Bid: %.2f' % impactBid,
            'Impact Ask: %.2f' % impactAsk,
            'Impact Mid: %.2f' % impactMid,
            'Fair Basis Rate: %.2f' % fairBasisRate,
            'Fair Basis: %.2f' % fairBasis,
            'Mark/Fair Price: %.2f' % markPrice,
        ]

    table = PrettyTable(['Key', 'BitMEX', 'Computed'])
    table.float_format = ".2"
    table.align = 'r'
    table.add_row(['Index Price', instrument['indicativeSettlePrice'], indexPrice])
    table.add_row(['Impact Bid', instrument['impactBidPrice'], impactBid])
    table.add_row(['Impact Ask', instrument['impactAskPrice'], impactAsk])
    table.add_row(['Impact Mid', instrument['impactMidPrice'], impactMid])
    table.add_row(['Fair Basis Rate', instrument['fairBasisRate'], fairBasis])
    table.add_row(['Fair Basis', instrument['fairBasis'], fairValue])
    table.add_row(['Fair Price', instrument['fairPrice'], fairPrice])
    print(table)

# Init
if __name__ == "__main__":
    main()
