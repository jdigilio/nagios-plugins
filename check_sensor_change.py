#!/usr/bin/python
# known limitation: if units don't match, it is ignored (eg, if temp alternates between C and F, this won't convert and you'll get a bunch of nagios warnings)

import argparse
import sqlite3
import re
import time
from scipy import stats

db = '/var/lib/nagios3/websensor.db'
dbconnection = None
dbcursor = None

ERRORS = { 'OK':0,
           'WARNING':1, 'WARN':1,
           'CRITICAL':2, 'CRIT':2,
           'UNKNOWN':3, 'UNK':3 }


# verify table exists or create it
def verify_db_and_table_exist():
    global dbconnection
    global dbcursor
    dbcursor.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?', ("table", "vals") )
    result = dbcursor.fetchone()
    if result == None:
        if args.verbose:
            print 'no table found. creating.'

        # Create table ### TODO: fix table name to use table_t
        dbcursor.execute('CREATE TABLE vals (date INT, host TEXT, sensor TEXT, val REAL, unit TEXT, attempts INT)')

        # Save (commit) the changes
        dbconnection.commit()


# get latest data from websensor
def read_sensor(sensor):
    import urllib2
    import lxml.html
    from lxml import etree
    import socket
    global args
    link = "http://%s/index.html?em345678" %(args.host)
    f = ''
    attempt = 0
    while attempt < args.retries:
        attempt += 1
        if args.debug:
            print "attempt: %d" %attempt
        try:
            f = urllib2.urlopen(link,timeout=args.timeout)
        except Exception, e:
            continue
            #TODO: this should be fixed with better error handling, eg:
            #need to raise on dns resolve failure
            #urllib2.URLError: <urlopen error [Errno -2] Name or service not known>
            #i think all others should just fallback to the retry
            #maybe keep errors in case of total failure?

        if type(f) is str:
            if args.debug:
                print "failed to open url. returned is a string"
            continue

        if args.debug:
            print f.getcode()
            print f.info()

        html = lxml.html.parse(f)
        a = html.xpath("body")
        body = etree.tostring(a[0])
        if args.debug:
            print body

        # only look for the sensor we want.
        # Ignore if other sensors don't read out correctly (illum seems to do that frequently)
        # <body>        EN1&#253;1TF: 76.5HU:37.9%IL   0.8                        </body>
        retvals = None
        if sensor == 't':
            sensor_name = 'temperature'
            result = re.search('T([A-Z]): *([0123456789.]+)HU',body)
            if result == None:
                continue
            else:
                retvals = (result.group(2),result.group(1))
                sensor_unit = 'deg'+retvals[1]
        elif sensor == 'h':
            sensor_name = 'humidity'
            result = re.search('HU:([0123456789.]+)(%)I',body)
            if result == None:
                continue
            else:
                retvals = (result.group(1),result.group(2))
                sensor_unit = retvals[1]
        elif sensor == 'i':
            sensor_name = 'illumination'
            result = re.search('%IL *([0123456789.]+)',body)
            if result == None:
                continue
            else:
                retvals = (result.group(1),'lx')
                sensor_unit = 'lx'
        else:
            cleanup_and_exit('UNKNOWN', 'unknown sensor %s' %(sensor))

        if retvals == None:
            continue
        if args.debug:
            print retvals
        return (sensor_name,sensor_unit,retvals,attempt)
    return (None,None,None,attempt)

def parseargs():
    # parse options (hostname, rate)
    parser = argparse.ArgumentParser(description='Calculate websensor rate of change. Warn or error if greater than rate.')
    parser.add_argument('--host', help='hostname of websensor', required=True)
    parser.add_argument('--sensor', help='t=temp,h=humidity,i=illumination', required=True, choices=['t','h','i'])
    parser.add_argument('--warnrate', help='warning rate of change for the sensor (eg for temp, 1/60 is "1 degree per 60 minutes")', required=True)
    parser.add_argument('--critrate', help='critical rate of change for the sensor (eg for temp, 2/60 is "2 degrees per 60 minutes"))', required=True)
    parser.add_argument('--timeout', help='timeout in connecting to host (default=60) (these things are finnicky... don\'t use less than 15)', type=int, default=60)
    parser.add_argument('--retries', help='times to retry connecting to host (default=3) (these things are finnicky... don\'t use less than 3)', type=int, default=5)
    parser.add_argument('--valuecorrection', help='adjust the value by this amount. useful mainly for testing', type=float, default=0)
    #parser.add_argument('--keepentries', help='', type=int, default=3)
    #parser.add_argument('--keephours', help='', type=int, default=3)
    parser.add_argument('--verbose', help='',action="store_true")
    parser.add_argument('--debug', help='used for development',action="store_true")

    return parser.parse_args()


def cleanup_and_exit(status,message):
    dbconnection.commit()
    dbconnection.close()
    print "%s: %s" %(status,message)
    exit(ERRORS[status])
    

def main():
    global args
    global db
    global dbconnection
    global dbcursor

    args = parseargs()

    if args.debug:
        print "##############################"

    #ratiore = re.compile('(\d+)/(\d+)')
    ratiore = re.compile('(?=.)([+-]?([0-9]*)(\.([0-9]+))?)/([+-]?([0-9]*)(\.([0-9]+))?)')
    wratiore = ratiore.match(args.warnrate)
    warn_degrees = abs(float(wratiore.group(1)))
    warn_minutes = float(wratiore.group(5))
    warn_seconds = 60.0 * warn_minutes
    if wratiore != None:
        wratio = warn_degrees / warn_seconds
    cratiore = ratiore.match(args.critrate)
    crit_degrees = abs(float(cratiore.group(1)))
    crit_minutes = float(cratiore.group(5))
    crit_seconds = 60.0 * crit_minutes
    if cratiore != None:
        cratio = crit_degrees / crit_seconds
    if args.debug:
        print "W:%f, C:%f" %(wratio,cratio)

    # connect to database
    dbconnection = sqlite3.connect(db)
    dbcursor = dbconnection.cursor()

    # verify db exists or create it (and parent dirs?)
    verify_db_and_table_exist()

    (sensor_name,sensor_unit,vals,attempts) = read_sensor(args.sensor)
    if vals == None:
        cleanup_and_exit('UNKNOWN', "error reading sensor")

    now = int(time.time())

    # insert latest into db
    dbcursor.execute("INSERT INTO vals (date,host,sensor,val,unit,attempts) VALUES (?,?,?,?,?,?)", (now,args.host,args.sensor,float(vals[0])+args.valuecorrection,vals[1],attempts))
    dbconnection.commit()

    # get historic data from db, from min_date to present
    # where min_date is now minus largest of times given by user in ratios (minus a minute for slack)
    min_date = now - 60*max(warn_minutes,crit_minutes) - args.retries*args.timeout - 60
    middle_date = now - 60*min(warn_minutes,crit_minutes) - args.retries*args.timeout - 60
    dbcursor.execute('SELECT date,val FROM vals WHERE date>? AND host=? AND sensor=? ORDER BY date', [min_date,args.host,args.sensor] ) #TODO: clean up, esp date
    results = dbcursor.fetchall()

    # lingress needs at least 3 datapoints
    if len(results) <= 2:
        cleanup_and_exit('UNKNOWN', "only got %d of 3 needed datapoints from database" %(len(results)))

    less_results = filter(lambda results: results[0]>middle_date, results)

    results_vals = [v for k,v in results]
    less_results_vals = [v for k,v in less_results]

    if args.debug:
        print min_date
        print results
        print middle_date
        print less_results

    # calculate rate over time
    #TODO: might want to re-work, combining with the min_date=...max()... and middle_date=...max()... sections above
    if warn_minutes != crit_minutes:
        if warn_minutes > crit_minutes:
            latest_wratio, intercept, r_value, p_value, std_err = stats.linregress(results)
            latest_cratio, intercept, r_value, p_value, std_err = stats.linregress(less_results)
        elif warn_minutes < crit_minutes:
            latest_wratio, intercept, r_value, p_value, std_err = stats.linregress(less_results)
            latest_cratio, intercept, r_value, p_value, std_err = stats.linregress(results)
        msg = "current %s change is %.2f%s over the past %dmin (warning alerts when abs(rate)-ge-%s), and is %.2f%s over the past %dmin (critical alerts when abs(rate)-ge-%s) | " \
              %(sensor_name, latest_wratio*60*warn_minutes, sensor_unit, warn_minutes, wratio*60*warn_minutes, latest_cratio*60*crit_minutes, sensor_unit, crit_minutes, cratio*60*crit_minutes)
    else:
        latest_wratio, intercept, r_value, p_value, std_err = stats.linregress(results)
        latest_cratio = latest_wratio
        msg = "current %s change is %.2f%s over the past %dmin (warning alerts when abs(rate)-ge-%s, critical alerts when abs(rate)-ge-%s) | " \
              %(sensor_name, latest_wratio*60*warn_minutes, sensor_unit, warn_minutes, wratio*60*warn_minutes, cratio*60*crit_minutes)

    if args.debug:
        print latest_wratio,latest_cratio

    # compare rate and alert if changing faster
    #msg = "current %s change for warning is %.2fdeg over the past %dmin (alerts when abs(rate)-ge-%s), and for critical is %.2fdeg over the past %dmin (alerts when abs(rate)-ge-%s) | " \
    #      %(sensor_name,latest_wratio*60*warn_minutes,warn_minutes,wratio*60*warn_minutes,latest_cratio*60*crit_minutes,crit_minutes,cratio*60*crit_minutes)
    if abs(latest_cratio) >= cratio:
        cleanup_and_exit('CRITICAL', msg + str(less_results_vals) )
    elif abs(latest_wratio) >= wratio:
        cleanup_and_exit('WARNING', msg + str(results_vals) )
    else:
        cleanup_and_exit('OK', msg + str(results_vals) )


if __name__== "__main__":
    main()
