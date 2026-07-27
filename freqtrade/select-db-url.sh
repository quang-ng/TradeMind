#!/bin/sh

case "${DRY_RUN:-true}" in
    true)
        FREQTRADE_DB_URL="sqlite:////freqtrade/db/tradesv3.dryrun.sqlite"
        ;;
    false)
        FREQTRADE_DB_URL="sqlite:////freqtrade/db/tradesv3.sqlite"
        ;;
    *)
        echo "DRY_RUN must be exactly 'true' or 'false'" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac

export FREQTRADE_DB_URL
