# -*- coding: utf-8 -*-
#@author: zoidberg

from __future__ import unicode_literals
from future import standard_library
standard_library.install_aliases()
from builtins import range
import re
from random import random
from urllib.parse import unquote
from urllib.parse import urlparse
from pycurl import FOLLOWLOCATION, LOW_SPEED_TIME
from pyload.plugins.internal.simplehoster import SimpleHoster, create_getInfo, PluginParseError, replace_patterns
from pyload.plugins.internal.captchaservice import ReCaptcha, SolveMedia
from pyload.utils import html_unescape
from pyload.network.requestfactory import get_url


class XFileSharingPro(SimpleHoster):
    """
    Common base for XFileSharingPro hosters like EasybytezCom, CramitIn, FiledinoCom...
    Some hosters may work straight away when added to __pattern__
    However, most of them will NOT work because they are either down or running a customized version
    """
    __name__ = "XFileSharingPro"
    __type__ = "hoster"
    __pattern__ = r'^unmatchable$'
    __version__ = "0.29"
    __description__ = """XFileSharingPro base hoster plugin"""
    __author_name__ = ("zoidberg", "stickell")
    __author_mail__ = ("zoidberg@mujmail.cz", "l.stickell@yahoo.it")

    FILE_NAME_PATTERN = r'<input type="hidden" name="fname" value="(?P<N>[^"]+)"'
    FILE_SIZE_PATTERN = r'You have requested .*\((?P<S>[\d\.\,]+) ?(?P<U>\w+)?\)</font>'
    FILE_INFO_PATTERN = r'<tr><td align=right><b>Filename:</b></td><td nowrap>(?P<N>[^<]+)</td></tr>\s*.*?<small>\((?P<S>[^<]+)\)</small>'
    FILE_OFFLINE_PATTERN = r'>\w+ (Not Found|file (was|has been) removed)'

    WAIT_PATTERN = r'<span id="countdown_str">.*?>(\d+)</span>'
    #LONG_WAIT_PATTERN = r'(?P<H>\d+(?=\s*hour))?.*?(?P<M>\d+(?=\s*minute))?.*?(?P<S>\d+(?=\s*second))?'
    OVR_DOWNLOAD_LINK_PATTERN = r'<h2>Download Link</h2>\s*<textarea[^>]*>([^<]+)'
    OVR_KILL_LINK_PATTERN = r'<h2>Delete Link</h2>\s*<textarea[^>]*>([^<]+)'
    CAPTCHA_URL_PATTERN = r'(http://[^"\']+?/captchas?/[^"\']+)'
    RECAPTCHA_URL_PATTERN = r'http://[^"\']+?recaptcha[^"\']+?\?k=([^"\']+)"'
    CAPTCHA_DIV_PATTERN = r'>Enter code.*?<div.*?>(.*?)</div>'
    SOLVEMEDIA_PATTERN = r'http:\/\/api\.solvemedia\.com\/papi\/challenge\.script\?k=(.*?)"'
    ERROR_PATTERN = r'class=["\']err["\'][^>]*>(.*?)</'

    def setup(self):
        if self.__name__ == "XFileSharingPro":
            self.__pattern__ = self.pyload.pluginmanager.hosterPlugins[self.__name__]['pattern']
            self.multiDL = True
        else:
            self.resumeDownload = self.multiDL = self.premium

        self.chunkLimit = 1

    def process(self, pyfile):
        self.prepare()

        pyfile.url = replace_patterns(pyfile.url, self.FILE_URL_REPLACEMENTS)

        if not re.match(self.__pattern__, pyfile.url):
            if self.premium:
                self.handleOverriden()
            else:
                self.fail("Only premium users can download from other hosters with %s" % self.HOSTER_NAME)
        else:
            try:
                # Due to a 0.4.9 core bug self.load would use cookies even if
                # cookies=False. Workaround using get_url to avoid cookies.
                # Can be reverted in 0.5 as the cookies bug has been fixed.
                self.html = get_url(pyfile.url, decode=True)
                self.file_info = self.getFileInfo()
            except PluginParseError:
                self.file_info = None

            self.location = self.getDirectDownloadLink()

            if not self.file_info:
                pyfile.name = html_unescape(unquote(urlparse(
                    self.location if self.location else pyfile.url).path.split("/")[-1]))

            if self.location:
                self.startDownload(self.location)
            elif self.premium:
                self.handlePremium()
            else:
                self.handleFree()

    def prepare(self):
        """ Initialize important variables """
        if not hasattr(self, "HOSTER_NAME"):
            self.HOSTER_NAME = re.match(self.__pattern__, self.pyfile.url).group(1)
        if not hasattr(self, "DIRECT_LINK_PATTERN"):
            self.DIRECT_LINK_PATTERN = r'(http://([^/]*?%s|\d+\.\d+\.\d+\.\d+)(:\d+)?(/d/|(?:/files)?/\d+/\w+/)[^"\'<]+)' % self.HOSTER_NAME

        self.captcha = self.errmsg = None
        self.passwords = self.getPassword().splitlines()

    def get_direct_download_link(self):
        """ Get download link for premium users with direct download enabled """
        self.req.http.lastURL = self.pyfile.url

        self.req.http.c.setopt(FOLLOWLOCATION, 0)
        self.html = self.load(self.pyfile.url, cookies=True, decode=True)
        self.header = self.req.http.header
        self.req.http.c.setopt(FOLLOWLOCATION, 1)

        location = None
        found = re.search(r"Location\s*:\s*(.*)", self.header, re.I)
        if found and re.match(self.DIRECT_LINK_PATTERN, found.group(1)):
            location = found.group(1).strip()

        return location

    def handle_free(self):
        url = self.getDownloadLink()
        self.logDebug("Download URL: %s" % url)
        self.startDownload(url)

    def get_download_link(self):
        for i in range(5):
            self.logDebug("Getting download link: #%d" % i)
            data = self.getPostParameters()

            self.req.http.c.setopt(FOLLOWLOCATION, 0)
            self.html = self.load(self.pyfile.url, post=data, ref=True, decode=True)
            self.header = self.req.http.header
            self.req.http.c.setopt(FOLLOWLOCATION, 1)

            found = re.search(r"Location\s*:\s*(.*)", self.header, re.I)
            if found:
                break

            found = re.search(self.DIRECT_LINK_PATTERN, self.html, re.S)
            if found:
                break

        else:
            if self.errmsg and 'captcha' in self.errmsg:
                self.fail("No valid captcha code entered")
            else:
                self.fail("Download link not found")

        return found.group(1)

    def handle_premium(self):
        self.html = self.load(self.pyfile.url, post=self.getPostParameters())
        found = re.search(self.DIRECT_LINK_PATTERN, self.html)
        if not found:
            self.parseError('DIRECT LINK')
        self.startDownload(found.group(1))

    def handle_overriden(self):
        #only tested with easybytez.com
        self.html = self.load("http://www.%s/" % self.HOSTER_NAME)
        action, inputs = self.parseHtmlForm('')
        upload_id = "%012d" % int(random() * 10 ** 12)
        action += upload_id + "&js_on=1&utype=prem&upload_type=url"
        inputs['tos'] = '1'
        inputs['url_mass'] = self.pyfile.url
        inputs['up1oad_type'] = 'url'

        self.logDebug(self.HOSTER_NAME, action, inputs)
        #wait for file to upload to easybytez.com
        self.req.http.c.setopt(LOW_SPEED_TIME, 600)
        self.html = self.load(action, post=inputs)

        action, inputs = self.parseHtmlForm('F1')
        if not inputs:
            self.parseError('TEXTAREA')
        self.logDebug(self.HOSTER_NAME, inputs)
        if inputs['st'] == 'OK':
            self.html = self.load(action, post=inputs)
        elif inputs['st'] == 'Can not leech file':
            self.retry(max_tries=20, wait_time=3 * 60, reason=inputs['st'])
        else:
            self.fail(inputs['st'])

        #get easybytez.com link for uploaded file
        found = re.search(self.OVR_DOWNLOAD_LINK_PATTERN, self.html)
        if not found:
            self.parseError('DIRECT LINK (OVR)')
        self.pyfile.url = found.group(1)
        header = self.load(self.pyfile.url, just_header=True)
        if 'location' in header:  # Direct link
            self.startDownload(self.pyfile.url)
        else:
            self.retry()

    def start_download(self, link):
        link = link.strip()
        if self.captcha:
            self.correctCaptcha()
        self.logDebug('DIRECT LINK: %s' % link)
        self.download(link, disposition=True)

    def check_errors(self):
        found = re.search(self.ERROR_PATTERN, self.html)
        if found:
            self.errmsg = found.group(1)
            self.logWarning(re.sub(r"<.*?>", " ", self.errmsg))

            if 'wait' in self.errmsg:
                wait_time = sum([int(v) * {"hour": 3600, "minute": 60, "second": 1}[u] for v, u in
                                 re.findall(r'(\d+)\s*(hour|minute|second)?', self.errmsg)])
                self.wait(wait_time, True)
            elif 'captcha' in self.errmsg:
                self.invalidCaptcha()
            elif 'premium' in self.errmsg and 'require' in self.errmsg:
                self.fail("File can be downloaded by premium users only")
            elif 'limit' in self.errmsg:
                self.wait(1 * 60 * 60, True)
                self.retry(25)
            elif 'countdown' in self.errmsg or 'Expired' in self.errmsg:
                self.retry()
            elif 'maintenance' in self.errmsg:
                self.tempOffline()
            elif 'download files up to' in self.errmsg:
                self.fail("File too large for free download")
            else:
                self.fail(self.errmsg)

        else:
            self.errmsg = None

        return self.errmsg

    def get_post_parameters(self):
        for _ in range(3):
            if not self.errmsg:
                self.checkErrors()

            if hasattr(self, "FORM_PATTERN"):
                action, inputs = self.parseHtmlForm(self.FORM_PATTERN)
            else:
                action, inputs = self.parseHtmlForm(input_names={"op": re.compile("^download")})

            if not inputs:
                action, inputs = self.parseHtmlForm('F1')
                if not inputs:
                    if self.errmsg:
                        self.retry()
                    else:
                        self.parseError("Form not found")

            self.logDebug(self.HOSTER_NAME, inputs)

            if 'op' in inputs and inputs['op'] in ('download2', 'download3'):
                if "password" in inputs:
                    if self.passwords:
                        inputs['password'] = self.passwords.pop(0)
                    else:
                        self.fail("No or invalid passport")

                if not self.premium:
                    found = re.search(self.WAIT_PATTERN, self.html)
                    if found:
                        wait_time = int(found.group(1)) + 1
                        self.setWait(wait_time, False)
                    else:
                        wait_time = 0

                    self.captcha = self.handleCaptcha(inputs)

                    if wait_time:
                        self.wait()

                self.errmsg = None
                return inputs

            else:
                inputs['referer'] = self.pyfile.url

                if self.premium:
                    inputs['method_premium'] = "Premium Download"
                    if 'method_free' in inputs:
                        del inputs['method_free']
                else:
                    inputs['method_free'] = "Free Download"
                    if 'method_premium' in inputs:
                        del inputs['method_premium']

                self.html = self.load(self.pyfile.url, post=inputs, ref=True)
                self.errmsg = None

        else:
            self.parseError('FORM: %s' % (inputs['op'] if 'op' in inputs else 'UNKNOWN'))

    def handle_captcha(self, inputs):
        found = re.search(self.RECAPTCHA_URL_PATTERN, self.html)
        if found:
            recaptcha_key = unquote(found.group(1))
            self.logDebug("RECAPTCHA KEY: %s" % recaptcha_key)
            recaptcha = ReCaptcha(self)
            inputs['recaptcha_challenge_field'], inputs['recaptcha_response_field'] = recaptcha.challenge(recaptcha_key)
            return 1
        else:
            found = re.search(self.CAPTCHA_URL_PATTERN, self.html)
            if found:
                captcha_url = found.group(1)
                inputs['code'] = self.decryptCaptcha(captcha_url)
                return 2
            else:
                found = re.search(self.CAPTCHA_DIV_PATTERN, self.html, re.DOTALL)
                if found:
                    captcha_div = found.group(1)
                    self.logDebug(captcha_div)
                    numerals = re.findall(r'<span.*?padding-left\s*:\s*(\d+).*?>(\d)</span>', html_unescape(captcha_div))
                    inputs['code'] = "".join([a[1] for a in sorted(numerals, key=lambda num: int(num[0]))])
                    self.logDebug("CAPTCHA", inputs['code'], numerals)
                    return 3
                else:
                    found = re.search(self.SOLVEMEDIA_PATTERN, self.html)
                    if found:
                        captcha_key = found.group(1)
                        captcha = SolveMedia(self)
                        inputs['adcopy_challenge'], inputs['adcopy_response'] = captcha.challenge(captcha_key)
                        return 4
        return 0


getInfo = create_getInfo(XFileSharingPro)
