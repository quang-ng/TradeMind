#!/bin/sh

# Fail closed on anything but an exact boolean: the value is substituted
# verbatim into config.json's order_types.stoploss_on_exchange
# (PROJECT.md Section 9.2), so a typo must stop the bot, not start it with
# a malformed or silently different stop setup.
case "${STOPLOSS_ON_EXCHANGE:-}" in
    true|false)
        ;;
    *)
        echo "STOPLOSS_ON_EXCHANGE must be exactly 'true' or 'false'" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac
