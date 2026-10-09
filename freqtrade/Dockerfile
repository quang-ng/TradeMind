FROM freqtradeorg/freqtrade:stable

USER root

COPY freqtrade/user_data /freqtrade/user_data
COPY freqtrade/docker-entrypoint.sh /entrypoint.sh
COPY freqtrade/select-db-url.sh /select-db-url.sh
RUN chmod +x /entrypoint.sh /select-db-url.sh && chown -R ftuser:ftuser /freqtrade/user_data

# Mount point for the freqtrade_data volume (SQLite trade DB). Docker seeds an
# empty named volume from the image directory's ownership; without this the
# mount point is created root:root and ftuser fails with sqlite3 "unable to
# open database file" on a fresh volume.
RUN mkdir -p /freqtrade/db && chown ftuser:ftuser /freqtrade/db

USER ftuser

ENTRYPOINT ["/entrypoint.sh"]
