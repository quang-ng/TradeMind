FROM freqtradeorg/freqtrade:stable

USER root

COPY freqtrade/user_data /freqtrade/user_data
COPY freqtrade/docker-entrypoint.sh /entrypoint.sh
COPY freqtrade/select-db-url.sh /select-db-url.sh
RUN chmod +x /entrypoint.sh /select-db-url.sh && chown -R ftuser:ftuser /freqtrade/user_data

USER ftuser

ENTRYPOINT ["/entrypoint.sh"]
