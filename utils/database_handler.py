import duckdb
import pandas as pd

class DatabaseHandler:
    def __init__(self, db_file=":memory:"):
        """
        Initializes the DatabaseHandler.

        Args:
            db_file (str): Path to the DuckDB database file.
                           Defaults to an in-memory database.
        """
        self.db_file = db_file
        self.conn = None
        self._connect()

    def _connect(self):
        """Establishes a connection to the DuckDB database."""
        try:
            self.conn = duckdb.connect(database=self.db_file, read_only=False)
            # print(f"Successfully connected to DuckDB: {self.db_file}")
        except Exception as e:
            print(f"Error connecting to DuckDB: {e}")
            self.conn = None # Ensure conn is None if connection fails
            raise # Re-raise the exception to signal failure

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
            # print(f"Disconnected from DuckDB: {self.db_file}")
            self.conn = None

    def execute_query(self, query, params=None):
        """
        Executes a SQL query that is expected to return results (e.g., SELECT).

        Args:
            query (str): The SQL query to execute.
            params (tuple, optional): Parameters to substitute into the query.

        Returns:
            list[tuple]: A list of tuples representing the rows fetched,
                         or None if an error occurs or no connection.
        """
        if not self.conn:
            print("Error: No database connection.")
            return None
        try:
            cursor = self.conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            return cursor.fetchall()
        except Exception as e:
            print(f"Error executing query '{query}': {e}")
            return None

    def execute_script(self, script_sql):
        """
        Executes a SQL script, which may contain multiple statements (e.g., CREATE TABLE, INSERT).
        This is generally for operations that do not return rows.

        Args:
            script_sql (str): The SQL script to execute.

        Returns:
            bool: True if execution was successful, False otherwise.
        """
        if not self.conn:
            print("Error: No database connection.")
            return False
        try:
            self.conn.execute(script_sql) # duckdb connection can execute scripts directly
            self.conn.commit() # Explicitly commit for non-SELECT statements if necessary
            return True
        except Exception as e:
            print(f"Error executing script: {e}")
            # Optionally, attempt a rollback if supported and appropriate
            # self.conn.rollback()
            return False

    def table_exists(self, table_name):
        """
        Checks if a table exists in the database.

        Args:
            table_name (str): The name of the table to check.

        Returns:
            bool: True if the table exists, False otherwise.
        """
        if not self.conn:
            print("Error: No database connection.")
            return False
        # Using DuckDB's information_schema for robustness
        query_duckdb = f"SELECT table_name FROM information_schema.tables WHERE table_name = ?;"
        results = self.execute_query(query_duckdb, (table_name,))
        return bool(results)


    def insert_dataframe(self, df, table_name, if_exists="append"):
        """
        Inserts a pandas DataFrame into a DuckDB table.

        Args:
            df (pd.DataFrame): The DataFrame to insert.
            table_name (str): The name of the target table.
            if_exists (str): How to behave if the table already exists.
                             Options: 'fail', 'replace', 'append'.
        Returns:
            bool: True if insertion was successful, False otherwise.
        """
        if not self.conn:
            print("Error: No database connection.")
            return False
        if not isinstance(df, pd.DataFrame):
            print("Error: Input is not a pandas DataFrame.")
            return False
        if df.empty:
            print("Warning: DataFrame is empty. Nothing to insert.")
            # For 'replace' on empty DF, we might want to ensure the table is dropped or empty.
            # For 'append', it's fine. For 'fail', it depends.
            # Let's make it so that an empty DF doesn't error out but might not create a table.
            if if_exists == "replace" and self.table_exists(table_name):
                 self.conn.execute(f"DELETE FROM {table_name}") # Empty the table
                 self.conn.commit()
            return True

        try:
            if if_exists == "replace":
                self.conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                self.conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
            elif if_exists == "append":
                if not self.table_exists(table_name):
                     self.conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
                else:
                    self.conn.execute(f"INSERT INTO {table_name} SELECT * FROM df")
            elif if_exists == "fail":
                if self.table_exists(table_name):
                    print(f"Error: Table {table_name} already exists and if_exists='fail'.")
                    return False
                self.conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM df")
            else:
                print(f"Error: Invalid value for if_exists: {if_exists}")
                return False

            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error inserting DataFrame into table {table_name}: {e}")
            # self.conn.rollback() # Consider rollback on error
            return False

    def get_dataframe(self, query, params=None):
        """
        Executes a query and returns the results as a pandas DataFrame.

        Args:
            query (str): The SQL query to execute.
            params (tuple, optional): Parameters to substitute into the query.

        Returns:
            pd.DataFrame: A DataFrame containing the query results,
                          or an empty DataFrame if an error occurs or no results.
        """
        if not self.conn:
            print("Error: No database connection. Returning empty DataFrame.")
            return pd.DataFrame()
        try:
            if params:
                return self.conn.execute(query, params).fetchdf()
            else:
                return self.conn.execute(query).fetchdf()
        except Exception as e:
            print(f"Error executing query '{query}' for DataFrame: {e}")
            return pd.DataFrame()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

if __name__ == "__main__":
    print("--- In-memory DB Example ---")
    try:
        with DatabaseHandler() as mem_db:
            mem_db.execute_script("CREATE TABLE test_mem (id INTEGER, name VARCHAR);")
            mem_db.execute_script("INSERT INTO test_mem VALUES (1, 'Jules'), (2, 'Verne');")
            rows_mem = mem_db.execute_query("SELECT * FROM test_mem;")
            if rows_mem: [print(row) for row in rows_mem]
            print(f"Table 'test_mem' exists: {mem_db.table_exists('test_mem')}")
            df_insert_mem = pd.DataFrame({'id': [3, 4], 'name': ['AI', 'Agent']})
            if mem_db.insert_dataframe(df_insert_mem, 'test_mem_df_table', if_exists='replace'):
                print("DataFrame inserted and retrieved (in-memory):\n", mem_db.get_dataframe("SELECT * FROM test_mem_df_table ORDER BY id;"))
            df_append = pd.DataFrame({'id': [5], 'name': ['Appended']})
            mem_db.insert_dataframe(df_append, 'test_mem_df_table', if_exists='append')
            print("After append (in-memory):\n", mem_db.get_dataframe("SELECT * FROM test_mem_df_table ORDER BY id;"))
    except Exception as e: print(f"Error in in-memory example: {e}")

    print("\n--- File-based DB Example (test_db.duckdb) ---")
    db_file_path = "test_db.duckdb"
    try:
        with DatabaseHandler(db_file=db_file_path) as file_db:
            file_db.execute_script("CREATE OR REPLACE TABLE test_file (id INTEGER, data FLOAT);")
            file_db.execute_script("INSERT INTO test_file VALUES (101, 1.23), (102, 4.56);")
            rows_file = file_db.execute_query("SELECT * FROM test_file;")
            if rows_file: [print(row) for row in rows_file]
            df_insert_file = pd.DataFrame({'id': [103, 104], 'data': [7.89, 0.12]})
            if file_db.insert_dataframe(df_insert_file, 'test_file_df_table', if_exists='replace'):
                print("DataFrame inserted and retrieved (file-based):\n", file_db.get_dataframe("SELECT * FROM test_file_df_table ORDER BY id;"))
    except Exception as e: print(f"Error in file-based example: {e}")
    finally:
        import os
        if os.path.exists(db_file_path): os.remove(db_file_path)
        if os.path.exists(db_file_path + ".wal"): os.remove(db_file_path + ".wal")
        print(f"Cleaned up {db_file_path}")
