# iquidus-sync
Alternate sync method for iquidus blockchain explorer.

## Install

```bash
pip install -r requirements.txt
```

## Run

This script reads the regular explorer ```settings.json``` to fetch its connection info to mongodb and the coin daemon.

```bash
./explorer_sync.py --explorer-config $HOME/explorer/settings.json
```

If you prefer to log to file:

```bash
./explorer_sync.py --explorer-config $HOME/explorer/settings.json --log-file=/tmp/sync.log
```
