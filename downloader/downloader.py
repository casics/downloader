#!/usr/bin/env python3
'''
downloader: download copies of repositories to local file system

This module contacts the CASICS server to get information about one or more
specified repositories, then downloads a copy of those repositories to the
local file system.
'''

import concurrent.futures

from   base64 import b64encode
from   datetime import datetime
import errno
import github3
from   halo import Halo
import http
import humanize
import locale
import magic
import os
import plac
from   pymongo import MongoClient
import requests
import shutil
import sys
import urllib
from   time import time, sleep
import wget
import zipfile

sys.path.append('..')

from common.casics import *
from common.messages import *
from common.credentials import *


# Global constants.
# .............................................................................

_CONN_TIMEOUT = 5000
'''Time to wait for connection to databases, in milliseconds.'''

_MAX_FAILURES = 10
'''Total number of failures allowed before we pause.'''

_MAX_RETRIES  = 3
'''Number of times we pause & retry after hitting the failure limit.'''

# GitHub defaults.

_GITHUB_KEYRING = 'org.casics.github'

# CASICS database defaults.

_CASICS_DEFAULT_HOST = 'localhost'
'''Default network host for CASICS server if no explicit host is given.'''

_CASICS_DEFAULT_PORT = 27017
'''Default network port for CASICS server if no explicit port number is given.'''

_CASICS_DB_NAME = 'github'
'''The name of the CASICS database.'''

_CASICS_KEYRING = "org.casics.casics"
'''The name of the keyring entry for LoCTerms client users.'''


# Main interface.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

@plac.annotations(
    file        = ('file containing repository identifiers',            'option', 'f'),
    id          = ('comma-separated list of repo ids on command line',  'option', 'i'),
    root        = ('root of directory where downloads will be written', 'option', 'r'),
    threads     = ('use given number of threads (default = 1)',         'option', 't'),
    casics_user = ('CASICS database user name',                         'option', 'u'),
    casics_pswd = ('CASICS database user password',                     'option', 'p'),
    casics_host = ('CASICS database server host',                       'option', 's'),
    casics_port = ('CASICS database connection port number',            'option', 'o'),
    github_user = ('GitHub database user name',                         'option', 'U'),
    github_pswd = ('GitHub database user password',                     'option', 'P'),
    nokeyring   = ('do not use a keyring',                              'flag',   'X'),
    nofrills    = ('do not show progress spinners or color output',     'flag',   'Y'),
)

def main(root=None, file=None, id=None, threads=1, nokeyring=False, nofrills=False,
         casics_user=None, casics_pswd=None, casics_host=None, casics_port=None,
         github_user=None, github_pswd=None):
    '''Download copies of repositories to local file system.

This module contacts the CASICS server to get information about one or more
specified repositories, then downloads a copy of those repositories to the
local file system.  The option `-r` is required, and one of the options `-f`
or `-i` must also be given.  Basic usage:

    downloader -r /path/to/downloads/root -f file-of-repo-identifiers.txt
or
    downloader -r /path/to/downloads/root -i ID,ID,ID,...

If a file of repository identifiers is given with `-f`, the file must have one
repository numeric identifier per line.  If a list of identifiers is given
with `-i` on the command line, it should be one or more numeric identifiers
separated by commas with no spaces between them.

Be kind when using threads.  Don't hit GitHub with a lot of simultaneous
downloads -- not only is it abusive of their resources, but you may also
risk being banned.

By default, this uses the operating system's keyring/keychain functionality
to get the user name and password needed to access both the CASICS database
server and GitHub over the network.  If no such credentials are found, it
will query the user interactively for the user name and password for each
system separately (so, two sets), and then store them in the keyring/keychain
(unless the -X argument is given) so that it does not have to ask again in
the future.  It is also possible to supply user names and passwords directly
using command line arguments, but this is discouraged because it is insecure
on multiuser computer systems. (Other users could run "ps" in the background
and see your credentials).

Additional arguments can be used to specify the host and port on which the
CASICS database.

The output will use spinners and color, unless the no-frills option `-Y` is
given.
    '''
    # Dealing with negated variables is confusing, so turn them around.
    keyring = not nokeyring
    fancy = not nofrills
    threads = int(threads)

    # Check arguments.
    if not root:
        raise SystemExit(colorcode('Need directory where to put downloads.', 'error', fancy))
    elif not os.path.exists(root):
        raise SystemExit(colorcode('"{}" does not exist.'.format(root), 'error', fancy))
    elif not os.path.isdir(root):
        raise SystemExit(colorcode('"{}" is not a directory.'.format(root), 'error', fancy))

    if id:
        id_list = [int(x) for x in id.split(',')]
    elif file:
        with open(file) as f:
            id_list = [int(x) for x in f.read().splitlines()]
    else:
        raise SystemExit(colorcode('Need either list or file of repository identifiers.',
                                   'error', fancy))

    if not (casics_user and casics_pswd and casics_host and casics_port):
        (casics_user, casics_pswd, casics_host, casics_port) = obtain_credentials(
            _CASICS_KEYRING, "CASICS", casics_user, casics_pswd, casics_host,
            casics_port, _CASICS_DEFAULT_HOST, _CASICS_DEFAULT_PORT)
    if not (github_user and github_pswd):
        (github_user, github_pswd, _, _) = obtain_credentials(
            _GITHUB_KEYRING, "GitHub", github_user, github_pswd, -1, -1)
    if keyring:
        # Save the credentials if they're different from what's saved.
        (s_user, s_pswd, s_host, s_port) = get_keyring_credentials(_CASICS_KEYRING)
        if s_user != casics_user or s_pswd != casics_pswd or \
           s_host != casics_host or s_port != casics_port:
            save_keyring_credentials(_CASICS_KEYRING, casics_user, casics_pswd,
                                     casics_host, casics_port)
        (s_user, s_pswd, _, _) = get_keyring_credentials(_GITHUB_KEYRING)
        if s_user != github_user or s_pswd != github_pswd:
            save_keyring_credentials(_GITHUB_KEYRING, github_user, github_pswd)
    if threads < 1:
        msg('Threads < 1 make no sense -- using threads = 1.', 'warning', fancy)
        threads = 1

    # Set locale so that file names end up with appropriate character encodings
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    # And we're ready to get to it.
    repos = get_repos(casics_user, casics_pswd, casics_host, casics_port)
    get_sources(repos, root, id_list, github_user, github_pswd, threads, fancy)


def get_sources(repos, downloads_root, id_list, user, password, threads, fancy):
    tmpdir = os.path.join(downloads_root, 'tmp')
    os.makedirs(tmpdir, exist_ok=True)

    msg('Starting {}.'.format(datetime.now().strftime("%Y-%m-%d %H:%M")), 'info', fancy)
    msg('Downloading {} repos to {}'.format(len(id_list), downloads_root), 'info', fancy)
    msg('Using temporary directory in {}'.format(tmpdir), 'info', fancy)
    msg('Using {} thread{}.'.format(threads, 's' if (threads > 1) else ''), 'info', fancy)

    fields = dict.fromkeys(['owner', 'name', 'default_branch'], 1)

    def do_download(id):
        entry = repos.find_one({'_id': id}, fields)
        if not entry:
            msg('*** skipping unknown GitHub id {}'.format(id), 'warning', fancy)
            return False
        return download(entry, tmpdir, downloads_root, user, password, threads > 1, fancy)

    count = 0
    failures = 0
    retries = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        start = time()
        for success in executor.map(do_download, iter(id_list)):
            failures += int(not success)
            if failures >= _MAX_FAILURES:
                # Try pause & continue in case of transient network issues.
                if retries <= _MAX_RETRIES:
                    retries += 1
                    msg('')
                    msg('*** Pausing because of too many consecutive failures',
                        'warning', fancy)
                    sleep(300 * retries)
                    failures = 0
                else:
                    # We've already paused & restarted.
                    msg('')
                    msg('*** Stopping because of too many consecutive failures',
                        'error', fancy)
                    break
            count += 1
            if count % 100 == 0:
                msg('{} [{:2f}]'.format(count, time() - start))
                start = time()
    msg('')
    msg('Done {}.'.format(datetime.now().strftime("%Y-%m-%d %H:%M")), 'info', fancy)


def download(entry, tmpdir, downloads_root, user, password, threaded, fancy):
    localpath = generate_path(downloads_root, entry['_id'])
    entry_info = e_summary(entry)

    # If we are NOT running multiple threads, we can have the spinner print
    # info about the repo before we start the process of trying to download.
    # That makes it clear what repo is being attempted at a given moment.
    # Unfortunately, if running multiple threads, the output will be jumbled
    # if we try to do that.  So, we have a bit of grungy output hacking here.

    spinner_prefix = '{} '.format(entry_info) if not threaded else ""
    def status(text, style):
        if fancy and not threaded:
            msg('{}'.format(text), style, fancy)
        else:
            msg('{} {}'.format(entry_info, text), style, fancy)

    with Halo(text=spinner_prefix, spinner='boxBounce', enabled=fancy):
        if os.path.exists(localpath) and os.listdir(localpath):
            status('already in {} -- skipping'.format(localpath), 'blue')
            return True
        # Try first with the default master branch.
        outfile = None
        start = datetime.now()
        try:
            url = "https://github.com/{}/{}/archive/{}.zip".format(
                entry['owner'], entry['name'], entry['default_branch'])
            outfile = wget.download(url, bar=None, out=tmpdir)
        except Exception as ex:
            # If we get a 404 from GitHub, it may mean there is no zip file for
            # what we think is the default branch.  To find out what it really
            # is, we first try scraping the web page, and if that fails, we
            # resort to using an API call.
            if hasattr(ex, 'code') and ex.code == 404:
                status('no zip file for branch "{}" -- looking for alternatives'
                       .format(entry['default_branch']), 'warning')
                newurl = get_archive_url_by_scraping(entry)
                if newurl:
                    try:
                        outfile = wget.download(newurl, bar=None, out=tmpdir)
                    except Exception as newe:
                        status('*** failed to download: {}' .format(str(newe)), 'error')
                        return False
                else:
                    newurl = get_archive_url_by_api(entry, user, password)
                    if newurl:
                        try:
                            outfile = wget.download(newurl, bar=None, out=tmpdir)
                        except Exception as newe:
                            status('*** failed to download: {}'.format(str(newe)), 'error')
                            return False
                    else:
                        status("can't find download URL for branch '{}'"
                               .format(entry['default_branch']), 'error')
                        return False
            else:
                status('*** failed to download: {}'.format(str(ex)), 'error')
                return False

        # Unzip it to a temporary path, then move it to the final location.

        filesize = file_size(outfile)
        try:
            outdir = unzip_archive(outfile, tmpdir)
            os.remove(outfile)
        except Exception as ex:
            msg('left zipped in {}: {}'.format(outfile, str(ex)), 'info')
            return False

        os.makedirs(os.path.dirname(localpath), exist_ok=True)
        os.rename(outdir, localpath)

        td = str(datetime.now() - start).split('.')[0]
        status('--> {}, {}, time {}'.format(localpath, filesize, td), 'info')
        return True


# Helpers.
# .............................................................................

def get_repos(user, password, host, port):
    db = MongoClient('mongodb://{}:{}@{}:{}/github?authSource=admin'
                     .format(user, password, host, port),
                     serverSelectionTimeoutMS=_CONN_TIMEOUT,
                     tz_aware=True, connect=True, socketKeepAlive=True)
    github_db = db[_CASICS_DB_NAME]
    repos_collection = github_db.repos
    return repos_collection


def unzip_archive(file, dest):
    # This only unzips what it guesses to be text files; for binary files,
    # it only creates the file name but leaves out the content.  (This is
    # our approach to saving disk space, because our analysis approach needs
    # text only.)  It returns the path to the directory where of the contents.

    zf = zipfile.ZipFile(file, 'r')
    infolist = zf.infolist()
    # The first item is the name of the root directory.
    if infolist:
        outdir = os.path.join(os.path.dirname(file), infolist[0].filename)
    for component in infolist:
        # zip files often cause problems on Linux because Linux file/dir
        # names don't allow many character encodings.  I thought that setting
        # the locale (see earlier in this file) would take care of that, but
        # it didn't.  That's the reason for the encode/decode wonkiness here.
        name = component.filename.encode('utf-8', 'ignore').decode('ascii', 'ignore')
        dir_name = os.path.join(dest, os.path.dirname(name))
        full_path = os.path.join(dir_name, os.path.basename(name))

        # Create missing directories.
        os.makedirs(dir_name, exist_ok=True)

        # Write files.
        try:
            if not name.endswith('/'):
                content = zf.read(component)
                if probably_text(name, content):
                    with open(full_path, 'wb') as fd:
                        fd.write(content)
                else:
                    os.mknod(full_path)
        except:
            continue

    zf.close()
    return outdir


def probably_text(filename, content):
    if not content:
        # Empty files are considered text.
        return True
    return (str(magic.from_buffer(content)).find('text') >= 0)


def file_size(path):
    return humanize.naturalsize(os.path.getsize(path))


def get_home_page_text(entry):
    url = 'http://github.com/' + entry['owner'] + '/' + entry['name']
    r = requests.get(url)
    return r.text if r.status_code == 200 else None


def get_archive_url_by_scraping(entry):
    html = get_home_page_text(entry)
    if not html:
        return None
    regionstart = html.find('<div class="file-navigation-option">')
    if regionstart < 1:
        return None
    regionend = html.find('Download ZIP', regionstart)
    pathstart = html.find('<a href="', regionstart, regionend)
    if not pathstart:
        return None
    pathstartlen = len('<a href="')
    pathend = html.find('"', pathstart + pathstartlen)
    return 'http://github.com/' + html[pathstart + pathstartlen + 1 : pathend]


def get_archive_url_by_api(entry, user, password):
    auth = '{0}:{1}'.format(user, password)
    headers = {
        'User-Agent': user,
        'Authorization': 'Basic ' + b64encode(bytes(auth, 'ascii')).decode('ascii'),
        'Accept': 'application/vnd.github.v3.raw',
    }
    url = "https://api.github.com/repos/{}/{}/zipball".format(
        entry['owner'], entry['name'])

    conn = http.client.HTTPSConnection("api.github.com")
    conn.request("GET", url, {}, headers)
    response = conn.getresponse()
    if response.status == 302:
        try:
            return next(y for x, y in response.getheaders() if x == 'Location')
        except:
            return None
    elif response.status == 200:
        return response.readall().decode('utf-8')
    else:
        return None


# For Emacs users
# ......................................................................
# Local Variables:
# mode: python
# python-indent-offset: 4
# End:
