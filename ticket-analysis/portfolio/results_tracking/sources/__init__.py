"""F9 result sources: manual-import only, never an automated fetch.

football_data_import.py is the one source implemented here. football-data.co.uk's
own terms restrict use to "private individuals only, NOT commercial or data
training products using automated bots/scrapers/AI" -- this package never
requests that site itself. The user downloads a CSV by hand and hands the local
file to import_football_data_csv(), which parses it and records provenance
(file hash, division/season/download time as the user states them); nothing
here reaches the network.
"""
