{
    "max_open_trades": -1,
    "stake_currency": "USDT",
    "stake_amount": "unlimited",
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": ${DRY_RUN},
    "dry_run_wallet": 10000,
    "db_url": "sqlite:////freqtrade/db/tradesv3.dryrun.sqlite",
    "cancel_open_orders_on_exit": false,
    "trading_mode": "spot",
    "unfilledtimeout": {
        "entry": 10,
        "exit": 10,
        "exit_timeout_count": 0,
        "unit": "minutes"
    },
    "entry_pricing": {
        "price_side": "same",
        "use_order_book": true,
        "order_book_top": 1,
        "price_last_balance": 0.0,
        "check_depth_of_market": {
            "enabled": false,
            "bids_to_ask_delta": 1
        }
    },
    "exit_pricing": {
        "price_side": "same",
        "use_order_book": true,
        "order_book_top": 1
    },
    "exchange": {
        "name": "binance",
        "key": "${BINANCE_API_KEY}",
        "secret": "${BINANCE_API_SECRET}",
        "ccxt_config": {},
        "ccxt_async_config": {},
        "pair_whitelist": ${PAIR_WHITELIST_JSON},
        "pair_blacklist": []
    },
    "pairlists": [{"method": "StaticPairList"}],
    "telegram": {"enabled": false, "token": "", "chat_id": ""},
    "api_server": {
        "enabled": true,
        "listen_ip_address": "0.0.0.0",
        "listen_port": 8080,
        "verbosity": "error",
        "enable_openapi": false,
        "jwt_secret_key": "${FREQTRADE_JWT_SECRET}",
        "CORS_origins": [],
        "username": "${FREQTRADE_API_USER}",
        "password": "${FREQTRADE_API_PASS}"
    },
    "webhook": {
        "enabled": true,
        "url": "${WEBHOOK_URL}",
        "format": "json",
        "entry": {
            "event": "entry", "trade_id": "{trade_id}", "pair": "{pair}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        },
        "entry_fill": {
            "event": "entry_fill", "trade_id": "{trade_id}", "pair": "{pair}",
            "open_rate": "{open_rate}", "amount": "{amount}", "open_date": "{open_date}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        },
        "entry_cancel": {
            "event": "entry_cancel", "trade_id": "{trade_id}", "pair": "{pair}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        },
        "exit": {
            "event": "exit", "trade_id": "{trade_id}", "pair": "{pair}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        },
        "exit_fill": {
            "event": "exit_fill", "trade_id": "{trade_id}", "pair": "{pair}",
            "close_rate": "{close_rate}", "amount": "{amount}",
            "profit_amount": "{profit_amount}", "profit_ratio": "{profit_ratio}",
            "close_date": "{close_date}", "exit_reason": "{exit_reason}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        },
        "exit_cancel": {
            "event": "exit_cancel", "trade_id": "{trade_id}", "pair": "{pair}",
            "secret": "${WEBHOOK_SHARED_SECRET}"
        }
    },
    "force_entry_enable": true,
    "initial_state": "running",
    "internals": {"process_throttle_secs": 5},
    "bot_name": "trademind-freqtrade"
}
