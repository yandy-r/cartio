# Customer onboarding checklist

What we send a customer before a Cartio assessment, so every access request looks the same. Part 1
is for the engineer. Everything from Part 2 on can be sent to the customer as is, after filling in
the placeholders in angle brackets.

## Part 1: before you send it (engineer)

- Fill in `<controller-host>` (the controller's host name) and `<collector-address>` (the
  collector's management address, once the customer gives it).
- Pick the data mode with the customer (Part 4). For On site, set up the store and the
  credentials on the collector (README, "On site customers"). On site customers can't get a
  report from the controller yet. Changing the mode after onboarding isn't built yet (YAN-1712).
- The device commands in Part 3 follow vendor documentation and haven't been run in our test lab
  yet (YAN-1647). Check them against the customer's software versions, and tell us in Linear if a
  command needs changing. On IOS-XE, check that `show running-config view full` works as the
  `cartio` user.
- Collector sizing and packaging are still open (YAN-1641).
- Parts 4 to 6 describe how v1 is designed to work. Secret masking during collection (YAN-1661,
  YAN-1753), the read-only command allow-list (YAN-1656) and per-collector keys with revocation
  (YAN-1650) have shipped. Before the pilot (YAN-1669), check anything else those parts promise
  and remove sentences about what hasn't shipped before you send.
- If the customer's proxy inspects TLS, write it down. How the collector works through an
  inspecting proxy is still being tested (YAN-1640).

## Part 2: what the collector needs

The collector is a small program that runs inside your network, reads your network devices and
sends the results to our controller. It connects out to our controller. Our controller never
connects into your network.

**A place to run.** One of these, in order of preference:

1. A virtual machine you provide, running Linux. As a guide, 2 vCPU, 4 GB RAM and 40 GB disk; we
   will confirm the sizing with you.
2. An existing jump host that can reach your management network.
3. Our engineer's laptop, connected to your network for the length of the assessment. This only
   suits the Hosted data mode (Part 4), because in On site mode the collector holds your data and
   device passwords.

**Outbound HTTPS to our controller.** From the collector:

- TCP 443 to `<controller-host>`. The connection stays open for long periods while the collector
  waits for work.
- DNS resolution of `<controller-host>`, or tell us if your egress rules need its IP address.
- Accurate time (NTP), because the connection is authenticated with certificates.

If outbound web traffic goes through a proxy, tell us its address and port. If the proxy decrypts
and inspects TLS, please exempt `<controller-host>` from inspection: the collector authenticates
with its own certificate, which an inspecting proxy breaks. If that isn't possible, tell us so we
can plan for it.

**Reach to your devices' management addresses.** From the collector:

| Protocol | Port | Used for |
| -- | -- | -- |
| SSH | TCP 22 | Read-only show commands on Cisco and FortiGate devices |
| HTTPS | TCP 443, or your FortiGate admin HTTPS port | Read-only FortiGate REST API calls |
| SNMP | UDP 161 | Basic inventory of any device, with SNMPv3 |
| ICMP | | Optional: lets discovery check that an address is up |

On site mode also needs reach to your data store (Part 4): TCP 5432 for PostgreSQL, TCP 445 for an
SMB share, or TCP and UDP 2049 for NFS.

**Where to start.** A list of seed addresses or networks to start discovery from, per site, such
as your core switches and firewalls, and any networks we must stay out of.

## Part 3: read-only accounts

We only ever read from your devices (Part 5), and we ask for accounts that can only read, so that
promise doesn't rest on our software alone. Use one account per access method, named `cartio` or
similar, so you can see our activity in your logs and remove it in one place. Where you can,
restrict each account and each management service to the collector's address.

Send passwords and keys through the channel we agree on, never by plain email. Once entered,
Cartio never shows a password or key back in its interface or API: it can only be replaced or
deleted.

### FortiGate

A read-only admin profile, used by an SSH admin and by a REST API user. Set each permission group
below to read; the group names vary a little between FortiOS versions. Read access to "WiFi &
Switch" lets us inventory the FortiSwitches and FortiAPs the FortiGate manages.

```text
config system accprofile
    edit "cartio-read"
        set secfabgrp read
        set ftviewgrp read
        set authgrp read
        set sysgrp read
        set netgrp read
        set loggrp read
        set fwgrp read
        set vpngrp read
        set utmgrp read
        set wanoptgrp read
        set wifi read
    next
end

config system admin
    edit "cartio"
        set accprofile "cartio-read"
        set trusthost1 <collector-address> 255.255.255.255
        set password <password>
    next
end

config system api-user
    edit "cartio-api"
        set accprofile "cartio-read"
        set vdom "root"
        config trusthost
            edit 1
                set ipv4-trusthost <collector-address> 255.255.255.255
            next
        end
    next
end

execute api-user generate-key cartio-api
```

The API key is shown once, when you generate it. With VDOMs, add `set scope global` to the profile
so we can read every VDOM, or list each VDOM we should read with `set vdom` on both the admin and
the API user.

### Cisco IOS and IOS-XE

The best option is one that allows `show` commands only:

- With TACACS+ or RADIUS, a `cartio` user whose command authorization permits `show` commands
  only.
- A parser view (role-based CLI) that contains only `show` commands, assigned with
  `username cartio view <view-name> secret <password>`.

Otherwise, a local account at its own privilege level. Below level 15, `show running-config` only
shows what the level could configure, so `view full` is allowed explicitly. This level also keeps
the basic user commands every level has, such as `ping`, `traceroute` and `terminal`.

```text
privilege exec all level 5 show
privilege exec level 5 show running-config view full
username cartio privilege 5 secret <password>
```

The vty lines need SSH and local login (`transport input ssh`, and `login local` or your AAA login
method). With `aaa new-model`, the account only starts at level 5 if `aaa authorization exec`
covers local users. To restrict SSH to the collector, use an `access-class` on the vty lines.

### SNMPv3

SNMPv3 with authentication and encryption (authPriv) and read access only. Use the strongest
algorithms your devices support, at least SHA and AES-128. We don't ask for SNMPv1 or v2c
communities. If a device only supports v2c, tell us and we'll agree what to do.

Cisco IOS and IOS-XE, restricted to the collector's address:

```text
ip access-list standard CARTIO-SNMP
 permit <collector-address>
snmp-server view CARTIO-RO iso included
snmp-server group CARTIO-RO v3 priv read CARTIO-RO access CARTIO-SNMP
snmp-server user cartio CARTIO-RO v3 auth sha <auth-password> priv aes 128 <priv-password>
```

FortiGate (FortiGate SNMP is read-only by design). Turn the SNMP agent on, create the user, and
allow SNMP on the interface the collector reaches. To restrict SNMP to the collector, use a
local-in policy, or the admin trusted hosts if you already rely on them.

```text
config system snmp sysinfo
    set status enable
end

config system snmp user
    edit "cartio"
        set trap-status disable
        set queries enable
        set security-level auth-priv
        set auth-proto sha256
        set auth-pwd <auth-password>
        set priv-proto aes256
        set priv-pwd <priv-password>
    next
end

config system interface
    edit "<management-interface>"
        append allowaccess snmp
    next
end
```

## Part 4: where your data is kept

You choose one of two data modes for your whole organization at onboarding. It applies to every
site and every assessment.

| | Hosted (the default) | On site |
| -- | -- | -- |
| Where your network data is stored | Our controller's database, kept separate from every other customer's | A store in your network that you choose: a PostgreSQL database, or a folder on an SMB or NFS share |
| Where device passwords and keys are stored | Our controller, encrypted with a key only your organization's data uses | The collector in your network. They never leave it. |
| What our controller keeps | Inventory, configurations with secrets masked, check results and reports | Only whether the collector is online and how its jobs went |
| How our engineers see your data | Through our controller, with one sign-in | Through our controller as a live view that is never stored there, or from inside your network over your own access |

In both modes, passwords, keys and SNMP secrets in device configurations are masked on the
collector, before the configuration leaves it.

**For On site, we also need:**

- The store: a PostgreSQL database with an account that can create and write tables in that one
  database, or a share and an account that can write to one folder on it.
- Reach from the collector to that store (Part 2).
- Your choice on the live view. With it on, your data passes through our controller in memory and
  is never stored there. With it off, our engineers only see your data from inside your network,
  over your own access (VPN, jump host or on site) to a page the collector serves. The live view
  stays off until you choose, and you can change it later.

If you need to change the data mode after onboarding, we do it with you as a recorded admin
action. Moving from Hosted to On site writes your data to your store first, and our controller
deletes its copy only after the collector confirms the copy in your store is complete.

## Part 5: what we never do

- **We never change your devices.** Cartio only runs read commands: show commands, API reads and
  SNMP reads. The collector refuses any other command, including configuration commands, API
  writes and SNMP sets. Our recommendations reach you as text in the report, and you decide
  whether to apply them.
- **We never connect into your network.** The collector connects out to our controller, so you
  don't open any inbound port from the internet for us. The only inbound access is your own, when
  you choose the On site inside view (Part 4).
- **We never mix customers.** Each collector belongs to exactly one customer and can only receive
  that customer's jobs and passwords.
- **We never show your passwords back.** Once entered, a password or key can be replaced or
  deleted, never displayed.

Every time a password is created, changed, used or deleted, it is recorded in an audit log for
your organization.

## Part 6: when the assessment ends

Tell us whether we will run more assessments. If not, or whenever you want to end our access:

1. Disable or delete the `cartio` accounts, the FortiGate API key and the SNMPv3 user.
2. Remove the collector VM, or tell us to remove it from the jump host.
3. Ask us to revoke the collector on our side, which also stops it reaching our controller.

## Checklist to send back

| Item | Your answer |
| -- | -- |
| Where the collector runs (your VM, jump host, or our laptop) | |
| Collector management address | |
| Web proxy address and port, if any. Does it inspect TLS? | |
| Seed addresses or networks per site, and networks to stay out of | |
| Device types and software versions (for example FortiOS 7.4, IOS-XE 17.9) | |
| FortiGate SSH admin and API user created | |
| Cisco show-only account created | |
| SNMPv3 user created | |
| How you'll send us the passwords and keys | |
| Data mode: Hosted or On site | |
| On site only: store type, address and account | |
| On site only: live view on or off | |
| Contact for access problems during the assessment | |
