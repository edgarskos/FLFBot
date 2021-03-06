#encoding=utf-8
from __future__ import unicode_literals
from __future__ import print_function 

import time
import re
from datetime import datetime, timedelta
import sqlite3
import mwclient
import urllib
from mwtemplates import TemplateEditor
import locale
from wp_private import botlogin, mailfrom, mailto, rollbar_token
import logging
import logging.handlers
import rollbar

rollbar.init(rollbar_token, 'production')  # access_token, environment

logger = logging.getLogger()
logger.setLevel(logging.INFO)
formatter = logging.Formatter('[%(asctime)s %(levelname)s] %(message)s')

smtp_handler = logging.handlers.SMTPHandler( mailhost = ('localhost', 25),
                fromaddr = mailfrom, toaddrs = mailto, 
                subject="[toolserver] CatWatchBot crashed!")
smtp_handler.setLevel(logging.ERROR)
logger.addHandler(smtp_handler)

file_handler = logging.handlers.RotatingFileHandler('ffbot.log', maxBytes=100000, backupCount=3)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


for loc in ['no_NO', 'nb_NO.utf8']:
    try:
        locale.setlocale(locale.LC_ALL, loc.encode('utf-8'))
    except locale.Error:
        pass

no = mwclient.Site(('https','no.wikipedia.org'), clients_useragent='FFBot. Run by User:Danmichaelo. Using mwclient/0.7.1')
no.login(*botlogin)

def find_rev(p, templates):
    #logger.info("    %s: " % (p)
    foundCleanRev = False
    revschecked = 0
    rev_id = -1
    rev_parent = -1
    while foundCleanRev == False:
        if rev_parent == 0:
            #logger.info('    %s: tagged from beginning (%s)' % (p,q))
            break
        elif rev_parent == -1:
            query = no.api('query', prop='revisions', rvprop='ids|timestamp|user|content|comment', rvdir='older', titles=p, rvlimit=10)['query']
        else:
            query = no.api('query', prop='revisions', rvprop='ids|timestamp|user|content|comment', rvdir='older', titles=p, rvlimit=10, rvstartid=rev_parent)['query']
        #print 'api call',rev_parent
        pid = query['pages'].keys()[0]
        if pid == '-1':
            #logger.info("(slettet, pid=-1)")
            break
        else:
            if 'revisions' in query['pages'][pid].keys():
                revs = query['pages'][pid]['revisions']
                for rev in revs:
                    revschecked += 1
                    #logger.debug(" checking (%s)"%rev['revid'])
                    if '*' in rev.keys() and 'user' in rev.keys():   # revision text and/or user may be hidden
                        txt = rev['*']
                        if txt.find('#OMDIRIGERING [[') != -1 or txt.find('#REDIRECT[[') != -1:
                            #logger.info('    %s: found redirect page' % (p))
                            #logger.info("   (omdirigeringsside) ")
                            foundCleanRev = True
                            rev_id = -1
                            break
                        foundCleanRev = True
                        for t in templates:
                            if re.search(r'{{\s*(mal:|template:)?%s'%t, txt, flags=re.IGNORECASE):
                                foundCleanRev = False
                        if foundCleanRev:
                            break
                        else:
                            rev_id = rev['revid']
                            rev_user = rev['user']
                            rev_comment = rev['comment']
                            rev_parent = rev['parentid']
                            rev_ts = rev['timestamp']
                    else:
                        rev_parent = rev['parentid']
    if rev_id == -1:
        #logger.warning('    %s: didn\'t find template for %s' % (p,q))
        #logger.info("Fant ikke merking!")
        return False
    else:
        rev_ts = datetime.strptime(rev_ts,'%Y-%m-%dT%H:%M:%SZ')
        return { 'id': rev_id, 'parent': rev_parent, 'date': rev_ts, 'user': rev_user, 'comment': rev_comment }
        
        #logger.info('    %s: found rev %s by %s (checked %d revisions)' % (p,lastrev,lastrevuser))
        #cur = self.sql.cursor()
        #if not self.dryrun:
        #    cur.execute(u'''INSERT INTO cleanlog (date, category, action, page, user, revision)
        #        VALUES(?,?,?,?,?,?)''', tuple([revts.strftime('%F %T'), catkey, q, p, lastrevuser, lastrev]))
        #cur.close()


sql = sqlite3.connect('ffbot.db')
cur = sql.cursor()
in_db = False


def main(catname, pagename, what, templates, table):
    pages = [c for c in no.categories[catname]]
    entries = []
    added = []
    removed = []
    template = templates[0]

    for p in pages:
        cur.execute('SELECT target, target2, date, revid, parentid, user, comment, reason FROM %s WHERE page=?' % table, [p.name])
        s = cur.fetchall()
        in_db = (len(s) > 0)
        logger.info('article: %s', p.name)
        if in_db:
            rev = { 'to': s[0][0], 'to2': s[0][1], 'date': datetime.strptime(s[0][2], '%Y-%m-%d'), 
                    'id': s[0][3], 'parent': s[0][4], 'user': s[0][5], 'comment': s[0][6], 'reason': s[0][7] }

        else:
            dp = TemplateEditor(p.text())
            t = None
            for tpl in templates:
                if tpl in dp.templates:
                    t = dp.templates[tpl][0]
                    break
            if t == None:
                logger.warn("> fant ikke noen mal")
                continue
            if not 1 in t.parameters:
                logger.warn(" > Ingen parametre gitt til malen!")
                continue

            logger.info(' -> %s', t.parameters[1])
            fra = '[[%s]]' % p.name

            rev = find_rev(p.name, templates)
            if rev == False:
                logger.warn(' fant ikke innsettingsrevisjonen for malen')
                continue

            rev['to'] = t.parameters[1].value.strip('[]')
            rev['to2'] = ''
            rev['reason'] = ''
            if 2 in t.parameters:
                logger.info(' begrunnelse: %s', t.parameters[2])
                rev['reason'] = t.parameters[2].value
            elif 'begrunnelse' in t.parameters:
                logger.info(' begrunnelse: %s', t.parameters['begrunnelse'])
                rev['reason'] = t.parameters['begrunnelse'].value

            if 'alternativ' in t.parameters:
                rev['to2'] += t.parameters['alternativ'].value

            vals = [p.name, rev['to'], rev['to2'], rev['date'].strftime('%F'), rev['id'], rev['parent'], rev['user'], rev['comment'], rev['reason'] ]
            cur.execute('INSERT INTO %s (page, target, target2, date, revid, parentid, user, comment, reason) VALUES (?,?,?,?,?,?,?,?,?)' % table, vals)
            added.append(p.name)

        #begrunnelse = "<span style='color:#999;'>''Ikke gitt''</span>"

        q = { 'title': p.name.encode('utf-8'), 'oldid': rev['id'], 'diff': 'prev' }
        link = '[%s Foreslått]' % (no.site['server'] + no.site['script'] + '?' + urllib.urlencode(q))
        #submitter = ''<br />%s' % (rev['user'], rev['user'], link)
        
        entry = ''
        if len(rev['reason']) != 0:
            entry += '<abbr style="color: #999; " title="Begrunnelse i mal">B:</span> %s<br />' % rev['reason']
        if len(rev['comment']) != 0:
            entry += '<abbr style="color: #999; " title="Redigeringsforklaring">R:</abbr> <nowiki>%s</nowiki><br />' % rev['comment']
        entry += "<small>''%s av [[Bruker:%s|%s]] den %s''</small>" % (link, rev['user'], rev['user'], rev['date'].strftime('%e. %B %Y'))

        fra = '[[:%s]]' % p.name
        til = '[[:%s]]' % rev['to']
        if len(rev['to2']) != 0:
            til += '<br />&nbsp;&nbsp; eller [[%s]]' % rev['to2']

        text = '|-\n| %s<br /> → %s \n| %s \n' % (fra, til, entry)
        entries.append([rev['date'], text])

        #time.sleep(1)
                
    sql.commit()

    # Remove processed entries from DB

    pnames = [p.name for p in pages]
    for row in cur.execute('SELECT page FROM %s' % table):
        n = row[0]
        if not n in pnames:
            removed.append(n)
    for n in removed:
        logger.info("Page %s found in db, but not in cat. Removing from db", n)
        cur.execute('DELETE FROM %s WHERE page=?' % table, [n])

    sql.commit()

    # Prepare output

    entries.sort(key = lambda x: x[0])
    text = '\n'.join(['<noinclude>',
        '{{Bruker:FLFBot/robotinfo|%s}}' % template, 
        '</noinclude>',
        '{| class="wikitable"',
        '|+ Sider merket for flytting vha. {{ml|%s}}' % template,
        '! Forslag !! Begrunnelse \n' + ''.join([e[1] for e in entries]) + '|}',
        '[[Kategori:Wikipedia-vedlikehold|%s]]' % what])

    summary = []
    if len(added) == 1:
        summary.append('Nytt %s: %s' % (what.lower(), added[0]))
    elif len(added) > 1:
        summary.append('%d nye %s' % (len(added), what.lower()))
    if len(removed) == 1:
        summary.append('%s behandlet: %s' % (what, removed[0]))
    elif len(removed) > 1:
        summary.append('%d %s behandlet' % (len(removed), what.lower()))

    if len(added) == 0 and len(removed) == 0:
        pass
        logger.info("Ingen endringer, avslutter")
    else:
        if pagename == None:
            print(text)
        else:
            page = no.pages[pagename]
            page.edit()
            page.save(text, ', '.join(summary))
            logger.info('Oppdaterte %s' % pagename)



try:
    main(catname='Artikler som bør flyttes', pagename='Wikipedia:Flytteforslag', what='Flytteforslag', templates=['Flytt', 'Flytting'], table='moves')
    # main(catname='Artikler som bør flettes', pagename=None, what='fletteforslag', templates=['flett', 'fletting', 'flett til', 'flett-til'], table='merges')

except IOError:
    rollbar.report_message('Got an IOError in the main loop', 'warning')
except:
    # catch all
    rollbar.report_exc_info()

