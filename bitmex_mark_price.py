#!/usr/bin/python3
#Written by swapman
#Telegram: @swapman
#Twitter: @whalepool
 
import os
import re
import datetime
from urllib.request import urlopen
import json
import math
import time
import dateutil.parser
 
#Initial setup parameters
 
SYMBOL="XBTU17"
IMPACT_NOTIONAL=10
 
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
 
#we're going to be scraping a few api endpoints so easier to use this little f
 
def scrapeurl(url):
    page = urlopen(url)
    data=page.read()
    decodedata=json.loads(data.decode())
    return decodedata
 
 
 
#######################################################################
# First let's grab some stats about the symbol instrument
 
instrument="https://www.bitmex.com/api/v1/instrument?symbol="+SYMBOL
 
decodedata2=scrapeurl(instrument)
 
#Set relevant vars from the scraped output
 
#We want to collect the prices that BitMEX reports
 
#Index
postedindexprice=decodedata2[0]['indicativeSettlePrice']
#Reported ImpactBidPrice
postedimpactbid=decodedata2[0]['impactMidPrice']
#Reported ImpactAskPrice
postedimpactask=decodedata2[0]['impactAskPrice']
#Reported ImpactMidPrice
postedimpactmid=decodedata2[0]['impactBidPrice']
#Reported FairBasis
postedfairbasis=decodedata2[0]['fairBasis']
#Reported FairBasisRate
postedfairbasisrate=decodedata2[0]['fairBasisRate']
#Reported Mark Price
postedmarkprice=decodedata2[0]['markPrice']
 
#Now we calculate the time to expiry by grabbing the expiry TS format and the current timestamp
expiryts=decodedata2[0]['expiry']
timestampts=decodedata2[0]['timestamp']
 
#Have to get into integer format from BitMEX's stamp format
expiryd=dateutil.parser.parse(expiryts)
timestampd=dateutil.parser.parse(timestampts)
 
#Round down to the seconds
timestampsec=round(timestampd.timestamp())
expirysec=round(expiryd.timestamp())
 
tteseconds=expirysec-timestampsec
 
#Finally, put it into days
ttedays=tteseconds/60/60/24 # This shows the value of days in a fraction down to the second. Maybe BitMEX rounds up or down?
TTE=ttedays #This will be used for the fair basis calculations at the bottom of script
print("Time to Expiry: "+str(TTE))
 
#Let's manually compute the BitMEX BTC/USD index for good measure
#As of now it's 50/50 GDAX and Bitstamp
 
urlb = "https://www.bitstamp.net/api/ticker"
decodedatab=scrapeurl(urlb)
stampprice=decodedatab['last'];
 
urlg = "https://api.gdax.com/products/BTC-USD/ticker"
decodedatag=scrapeurl(urlg)
gdaxprice=decodedatag['price'];
 
indexprice=(float(stampprice)+float(gdaxprice))/2;
 
 
#Grab the Orderbook so we can grab the depth for bids and asks for impact prices
 
url = "https://www.bitmex.com/api/v1/orderBook?symbol="+SYMBOL+"&depth=200"
decodedata=scrapeurl(url)
 
#Set base values for the amount of bids & asks left until hittign IMPACT_NOTIONAL in BTC value
 
bidsleft=0
asksleft=0
 
#Set the base value for the impact bid and asks prices
 
impactbid=0
impactask=0
 
#Set switch for when the IMPACT_NOTIONAL for bids and asks is hit by the script
 
bidsdone=0
asksdone=0
 
######################################################################
# The way we compute Impact Prices is by going into either side      #
# of the book for 10 BTC worth of order values, and take the average #
# price filled.                                                      #
######################################################################
 
 
#To do this, we loop over every orderbook level from BitMEX for the instrument
 
for x in decodedata:
    level = x['level']
 
    #Code for Impact bid
   
    bidsize=x['bidSize']
    bidprice=x['bidPrice']
    #make sure the parms are both not null
    if bidsize is not None and bidprice is not None:
        # What is most important with inverse futures is notional BTC value, so we compute the BTC order value
        bidbtc=bidsize/bidprice
   
    #Check that the amount of bids left until IMPACT_NOTIONAL is below the IMPACT_NOTIONAL
    if bidsleft<IMPACT_NOTIONAL:
        #Add to the running count of how many BTC is left in comparison to IMPACT_NOTIONAL
        bidsleft=bidsleft+bidbtc
 
        #The average price is the bid price at each level weighted by the level's BTC size as proportion of IMPACT_NOTIONAL
        impactbid=impactbid+(bidbtc/IMPACT_NOTIONAL)*bidprice
       
        #If the current order level pushes above IMPACT_NOTIONAL count, then we re-compute the impactbid
        if bidsleft>IMPACT_NOTIONAL:
            impactbid=impactbid-(bidbtc/IMPACT_NOTIONAL)*bidprice
 
            # Wind back to prior level
 
            priorlastbid=bidsleft-bidbtc
 
            # We only want to have the last amount up to IMPACT_NOTIONAL, not the part above
 
            lastbid=IMPACT_NOTIONAL-priorlastbid
 
            # This will be the impact bid instead
            impactbid=impactbid+bidprice*(lastbid/IMPACT_NOTIONAL)
 
 
    #Code for Impact ask
    #See comments above, same exact format
    asksize=x['askSize']
    askprice=x['askPrice']
    if asksize is not None and askprice is not None:
        askbtc=asksize/askprice
 
    if asksleft<IMPACT_NOTIONAL:
        asksleft=asksleft+askbtc
 
        impactask=impactask+(askbtc/IMPACT_NOTIONAL)*askprice
       
        if asksleft>IMPACT_NOTIONAL:  
            impactask=impactask-(askbtc/IMPACT_NOTIONAL)*askprice
            priorlastask=asksleft-askbtc
            lastask=IMPACT_NOTIONAL-priorlastask
            impactask=impactask+askprice*(lastask/IMPACT_NOTIONAL)
 
    #impact mid
 
    impactmid=(impactask+impactbid)/2
 
#Impact Mid computation matches up close (but not perfect) with BitMEX's posted
 
 
#Fair price calculation
 
#From the BitMEX site https://www.bitmex.com/app/fairPriceMarking :
 
#% Fair Basis = (Impact Mid Price / Index Price - 1) / (Time To Expiry / 365)
#Fair Value   = Index Price * % Fair Basis * (Time to Expiry / 365)
#Fair Price   = Index Price + Fair Value
 
fairbasis=(impactmid/indexprice-1)/(TTE/365)
fairvalue=indexprice*fairbasis*(TTE/365)
fairprice=indexprice+fairvalue
 
 
 
 
 
print("BitMEX numbers:\nIndex price: "+str(postedindexprice)+"\nImpact bid: "+str(postedimpactbid)+"\nImpact ask: "+str(postedimpactask)+"\nImpact mid: "+str(postedimpactmid)+"\n"+"Fair basis rate: "+str(postedfairbasisrate)+"\nFair basis: "+str(postedfairbasis)+"\nMark price: "+str(postedmarkprice)+"\n")
 
print("Computed numbers:\nIndex price: "+str(indexprice)+"\nImpact bid: "+str(impactbid)+"\nImpact ask: "+str(impactask)+"\nImpact mid: "+str(impactmid)+"\nFair basis rate: "+str(fairbasis)+"\nFair basis: "+str(fairvalue)+"\nFair price: "+str(fairprice))
