# -*- coding: utf-8 -*-
#@author: RaNaN

from __future__ import unicode_literals
from builtins import object
from threading import Lock
from random import choice

from pyload.Api import AccountInfo
from pyload.utils import lock, json


class AccountManager(object):
    """manages all accounts"""

    def __init__(self, core):
        """Constructor"""

        self.pyload = core
        self.lock = Lock()

        # PluginName mapped to list of account instances
        self.accounts = {}

        self.loadAccounts()

    def _create_account(self, info, password, options):
        plugin = info.plugin
        loginname = info.loginname
        # Owner != None must be enforced
        if info.owner is None:
            raise ValueError("Owner must not be null")

        klass = self.pyload.pluginmanager.load_class("account", plugin)
        if not klass:
            self.pyload.log.warning(_("Account plugin %s not available") % plugin)
            raise ValueError("Account plugin %s not available" % plugin)

        if plugin not in self.accounts:
            self.accounts[plugin] = []

        self.pyload.log.debug("Create account %s:%s" % (plugin, loginname))

        # New account instance
        account = klass.fromInfoData(self, info, password, options)
        self.accounts[plugin].append(account)
        return account

    def load_accounts(self):
        """loads all accounts available from db"""
        for info, password, options in self.pyload.db.load_accounts():
            # put into options as used in other context
            options = json.loads(options) if options else {}
            try:
                self._create_account(info, password, options)
            except Exception:
                self.pyload.log.error(_("Could not load account %s") % info)
                self.pyload.print_exc()

    def iter_accounts(self):
        """ yields login, account  for all accounts"""
        for plugin, accounts in self.accounts.items():
            for account in accounts:
                yield plugin, account

    def save_accounts(self):
        """save all account information"""
        data = []
        for plugin, accounts in self.accounts.items():
            data.extend(
                [(acc.loginname, 1 if acc.activated else 0, 1 if acc.shared else 0, acc.password,
                  json.dumps(acc.options), acc.aid) for acc in
                 accounts])
        self.pyload.db.save_accounts(data)

    def get_account(self, aid, plugin, user=None):
        """ Find a account by specific user (if given) """
        if plugin in self.accounts:
            for acc in self.accounts[plugin]:
                if acc.aid == aid and (not user or acc.owner == user.true_primary):
                    return acc

    @lock
    def create_account(self, plugin, loginname, password, uid):
        """ Creates a new account """

        aid = self.pyload.db.create_account(plugin, loginname, password, uid)
        info = AccountInfo(aid, plugin, loginname, uid, activated=True)
        account = self._create_account(info, password, {})
        account.scheduleRefresh()
        self.saveAccounts()

        self.pyload.eventmanager.dispatch_event("account:created", account.toInfoData())
        return account

    @lock
    def update_account(self, aid, plugin, loginname, password, user):
        """add or update account"""
        account = self.getAccount(aid, plugin, user)
        if not account:
            return

        if account.setLogin(loginname, password):
            self.saveAccounts()
            account.scheduleRefresh(force=True)

        self.pyload.eventmanager.dispatch_event("account:updated", account.toInfoData())
        return account

    @lock
    def remove_account(self, aid, plugin, uid):
        """remove account"""
        if plugin in self.accounts:
            for acc in self.accounts[plugin]:
                # admins may delete accounts
                if acc.aid == aid and (not uid or acc.owner == uid):
                    self.accounts[plugin].remove(acc)
                    self.pyload.db.remove_account(aid)
                    self.pyload.evm.dispatch_event("account:deleted", aid, user=uid)
                    break

    @lock
    def select_account(self, plugin, user):
        """ Determines suitable plugins and select one """
        if plugin in self.accounts:
            uid = user.true_primary if user else None
            # TODO: temporary allowed None user
            accs = [x for x in self.accounts[plugin] if x.isUsable() and (x.shared or uid is None or x.owner == uid)]
            if accs: return choice(accs)

    @lock
    def get_all_accounts(self, uid):
        """ Return account info for every visible account """
        # filter by owner / shared, but admins see all accounts
        accounts = []
        for plugin, accs in self.accounts.items():
            accounts.extend([acc for acc in accs if acc.shared or not uid or acc.owner == uid])

        return accounts

    def refresh_all_accounts(self):
        """ Force a refresh of every account """
        for p in self.accounts.values():
            for acc in p:
                acc.getAccountInfo(True)
