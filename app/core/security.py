"""Security helpers placeholder.

VCC Plastics does not perform any local password hashing or token signing
today: authentication is delegated entirely to the external VCC Group API
(see ``app.integrations.vcc_group``). This module is a placeholder reserved
for future local security helpers - e.g. password hashing or JWT signing -
should VCC Plastics ever need to authenticate users independently of VCC
Group. Adding real logic here today would have no effect unless it is wired
into a router, so nothing currently imports from this module.
"""
