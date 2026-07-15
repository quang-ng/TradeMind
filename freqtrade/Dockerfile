FROM freqtradeorg/freqtrade:stable

USER root

COPY freqtrade/user_data /freqtrade/user_data
COPY freqtrade/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R ftuser:ftuser /freqtrade/user_data

USER ftuser

ENTRYPOINT ["/entrypoint.sh"]
