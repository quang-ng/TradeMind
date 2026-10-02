# Pinned, not :stable — ExternalSignalStrategy.ft_stoploss_reached overrides a
# Freqtrade-internal method and the stoploss_on_exchange behaviour it relies on
# was verified against this exact version (PROJECT.md Section 9.2). Re-check
# both before bumping.
FROM freqtradeorg/freqtrade:2026.8

USER root

COPY freqtrade/user_data /freqtrade/user_data
COPY freqtrade/docker-entrypoint.sh /entrypoint.sh
COPY freqtrade/select-db-url.sh /select-db-url.sh
COPY freqtrade/validate-stoploss-on-exchange.sh /validate-stoploss-on-exchange.sh
RUN chmod +x /entrypoint.sh /select-db-url.sh /validate-stoploss-on-exchange.sh && chown -R ftuser:ftuser /freqtrade/user_data

USER ftuser

ENTRYPOINT ["/entrypoint.sh"]
