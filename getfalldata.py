import pandas as pd
import sqlite3

EXCEL_DB = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/Data for 10U 2026-2027 fall season.xlsx"
SQLITE_DB = "C:/Users/bsmel/OneDrive/Documents/Baseball_data/10u data.db"

data_in = pd.read_excel(EXCEL_DB,sheet_name='final',header=0)

df_in = pd.DataFrame(data_in)

with sqlite3.connect(SQLITE_DB) as conn:
    df_in.to_sql('events',conn,if_exists='append',index=False)

