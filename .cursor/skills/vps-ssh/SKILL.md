---
name: vps-ssh
description: >-
  SSH access to cock-monitor VPS hosts: New York (cock-is), Madrid
  (83.147.242.226), Germany (cock-germany), London (cock-london), Helsinki
  (cock-helsinki, 163.5.153.32), RF1 (whitelisthack, 178.253.44.178), RF2
  (rf2, 132.243.16.81). Use when the user says connect to the server/VPS, or
  mentions Нью-Йорк, Америка, США, cock-is, Мадрид, Madrid, Германия,
  Germany, Лондон, London, Хельсинки, Helsinki, РФ1, РФ2, РФ, Russia,
  whitelisthack, rf2, or any of these IPs.
---

# VPS SSH

## Servers

| Nickname (user says) | SSH target | IP | Port | Key |
|----------------------|------------|-----|------|-----|
| **Нью-Йорк**, **Нью Йорк**, **Америка**, **США**, **USA**, **NY**, `cock-is` | `cock-is` | `153.75.246.28` | 22 | `~/.ssh/cock-is` |
| **Мадрид**, **Madrid** | `root@83.147.242.226` | `83.147.242.226` | 22 | `~/.ssh/madrid` |
| **Германия**, **Germany**, `cock-germany` | `cock-germany` | `144.31.154.44` | 22 | `~/.ssh/germany` |
| **Лондон**, **London**, `cock-london` | `cock-london` | `163.5.41.47` | 22 | `~/.ssh/london` |
| **Хельсинки**, **Helsinki**, `cock-helsinki` | `cock-helsinki` | `163.5.153.32` | 22 | `~/.ssh/helsinki` |
| **РФ1**, **РФ**, `whitelisthack` | `whitelisthack` | `178.253.44.178` | 22 | `~/.ssh/whitelisthack` |
| **РФ2**, `rf2` | `rf2` | `132.243.16.81` | 22 | `~/.ssh/rf2` |

### New York

| Parameter | Value |
|-----------|-------|
| SSH alias | `cock-is` |
| User | `root` |
| Hostname on server | `vps3433579.trouble-free.net` |

Known stack: Ubuntu 26.04, 3x-ui (`x-ui`), cock-monitor at `/opt/cock-monitor`.

### Madrid

| Parameter | Value |
|-----------|-------|
| SSH command | `ssh root@83.147.242.226` |
| User | `root` |

Use the **IP form** `root@83.147.242.226`, not an alias. Key from `~/.ssh/config` (`IdentityFile ~/.ssh/madrid`).

### Germany

| Parameter | Value |
|-----------|-------|
| SSH alias | `cock-germany` |
| Equivalent | `ssh root@144.31.154.44` |
| User | `root` |

Prefer alias **`cock-germany`** — key is in `~/.ssh/config`.

### London

| Parameter | Value |
|-----------|-------|
| SSH alias | `cock-london` |
| Equivalent | `ssh root@163.5.41.47` |
| User | `root` |

Prefer alias **`cock-london`** — key is in `~/.ssh/config`.

### Helsinki

| Parameter | Value |
|-----------|-------|
| SSH alias | `cock-helsinki` |
| Equivalent | `ssh root@163.5.153.32` |
| User | `root` |
| Key | `~/.ssh/helsinki` |

In `~/.ssh/config` as `Host 163.5.153.32 cock-helsinki`. Prefer alias **`cock-helsinki`**.

### RF1 (РФ1)

| Parameter | Value |
|-----------|-------|
| SSH alias | `whitelisthack` |
| Equivalent | `ssh root@178.253.44.178` |
| User | `root` |
| Key | `~/.ssh/whitelisthack` |

Already in `~/.ssh/config` as `Host 178.253.44.178 whitelisthack`. Prefer alias **`whitelisthack`**.

Note: config allows `publickey,password` — if key auth fails in `BatchMode`, user may need password interactively.

### RF2 (РФ2)

| Parameter | Value |
|-----------|-------|
| SSH alias | `rf2` |
| Equivalent | `ssh root@132.243.16.81` |
| User | `root` |
| Key | `~/.ssh/rf2` |

Add to `~/.ssh/config`:

```
Host 132.243.16.81 rf2
  HostName 132.243.16.81
  User root
  IdentityFile ~/.ssh/rf2
  IdentitiesOnly yes
```

Prefer alias **`rf2`**.

## Routing phrases → host

| User means | Connect as |
|------------|------------|
| Нью-Йорк, Нью Йорк, Америка, США, USA, NY, cock-is, `153.75.246.28` | `cock-is` |
| Мадрид, Madrid, `83.147.242.226` | `root@83.147.242.226` |
| Германия, Germany, cock-germany, `144.31.154.44` | `cock-germany` |
| Лондон, London, cock-london, `163.5.41.47` | `cock-london` |
| Хельсинки, Helsinki, cock-helsinki, `163.5.153.32` | `cock-helsinki` |
| РФ1, РФ, whitelisthack, `178.253.44.178` | `whitelisthack` |
| РФ2, rf2, `132.243.16.81` | `rf2` |
| «сервер», «VPS», «на сервере» **без города** | **Спросить**: Нью-Йорк, Мадрид, Германия, Лондон, Хельсинки, РФ1 или РФ2? Если из контекста чата ясен хост — использовать его |

## Agent behavior

1. **Run commands yourself** via SSH — do not only print the ssh command unless the user wants manual steps.
2. Non-interactive runs (agent/tools):

```bash
ssh -o BatchMode=yes -o ConnectTimeout=15 cock-is 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 root@83.147.242.226 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 cock-germany 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 cock-london 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 cock-helsinki 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 whitelisthack 'команда'
ssh -o BatchMode=yes -o ConnectTimeout=15 rf2 'команда'
```

3. Interactive-only tasks (wizards, passwords) — user runs locally:

```bash
ssh cock-is
ssh root@83.147.242.226
ssh cock-germany
ssh cock-london
ssh cock-helsinki
ssh whitelisthack
ssh rf2
```

## Deploy env vars

```bash
# New York
export DEPLOY_HOST=cock-is

# Madrid
export DEPLOY_HOST=root@83.147.242.226

# Germany
export DEPLOY_HOST=cock-germany

# London
export DEPLOY_HOST=cock-london

# Helsinki
export DEPLOY_HOST=cock-helsinki

# RF1 (РФ1)
export DEPLOY_HOST=whitelisthack

# RF2 (РФ2)
export DEPLOY_HOST=rf2
```

Common on all hosts:

```bash
export APP_DIR=/opt/cock-monitor
export ENV_FILE=/etc/cock-monitor.env
```

Then:

```bash
ssh "$DEPLOY_HOST" "cd $APP_DIR && git status --short --branch"
```

## Examples

```bash
ssh -o BatchMode=yes cock-is 'df -h /'
ssh -o BatchMode=yes root@83.147.242.226 'df -h /'
ssh -o BatchMode=yes cock-germany 'df -h /'
ssh -o BatchMode=yes cock-london 'df -h /'
ssh -o BatchMode=yes cock-helsinki 'df -h /'
ssh -o BatchMode=yes whitelisthack 'df -h /'
ssh -o BatchMode=yes rf2 'df -h /'
```

## Related skills

- Disk cleanup: [server-disk-cleanup](../server-disk-cleanup/SKILL.md)
- Deploy runbook: [DEPLOY.md](../../../DEPLOY.md)
