import pytest
import pandas as pd
from pandas.testing import assert_frame_equal
import os
from utils.database_handler import DatabaseHandler

# Fixture for an in-memory DatabaseHandler instance
@pytest.fixture
def mem_db():
    db = DatabaseHandler() # In-memory by default
    yield db
    db.close()

# Fixture for a file-based DatabaseHandler instance
@pytest.fixture
def file_db(tmp_path):
    db_file = tmp_path / "test_temp.duckdb"
    db = DatabaseHandler(db_file=str(db_file))
    yield db
    db.close()
    # wal file might also be created by duckdb
    wal_file = str(db_file) + ".wal"
    if os.path.exists(wal_file):
        try:
            os.remove(wal_file)
        except OSError: # pragma: no cover
            pass # Ignore if somehow locked, though tmp_path should handle it

def test_can_import_database_handler():
    assert DatabaseHandler is not None

def test_connection_in_memory(mem_db):
    assert mem_db.conn is not None
    assert mem_db.db_file == ":memory:"

def test_connection_file_based(file_db):
    assert file_db.conn is not None
    assert "test_temp.duckdb" in file_db.db_file

def test_close_connection(mem_db):
    mem_db.close()
    assert mem_db.conn is None
    # Test closing an already closed connection
    mem_db.close()
    assert mem_db.conn is None


def test_context_manager(tmp_path):
    db_file_path = str(tmp_path / "context_test.duckdb")
    with DatabaseHandler(db_file=db_file_path) as db:
        assert db.conn is not None
        db.execute_script("CREATE TABLE test_context (id INT);")
        assert db.table_exists("test_context")
    assert db.conn is None
    assert os.path.exists(db_file_path)


def test_execute_script_create_table(mem_db):
    sql_script = "CREATE TABLE users (id INTEGER, name VARCHAR);"
    assert mem_db.execute_script(sql_script) is True
    assert mem_db.table_exists("users") is True

def test_execute_script_insert_data(mem_db):
    mem_db.execute_script("CREATE TABLE items (id INTEGER, description VARCHAR);")
    insert_script = "INSERT INTO items VALUES (1, 'Test Item'), (2, 'Another Item');"
    assert mem_db.execute_script(insert_script) is True
    results = mem_db.execute_query("SELECT COUNT(*) FROM items;")
    assert results[0][0] == 2

def test_execute_script_invalid_sql(mem_db, capsys):
    assert mem_db.execute_script("CREATE TABLE (id INTEGER);") is False
    captured = capsys.readouterr()
    assert "Error executing script" in captured.out # Check for error message

def test_execute_query_select_data(mem_db):
    mem_db.execute_script("CREATE TABLE data_table (value INTEGER);")
    mem_db.execute_script("INSERT INTO data_table VALUES (100), (200);")
    results = mem_db.execute_query("SELECT value FROM data_table WHERE value > 150;")
    assert results == [(200,)]

def test_execute_query_with_params(mem_db):
    mem_db.execute_script("CREATE TABLE query_param_table (id INT, name TEXT);")
    mem_db.execute_script("INSERT INTO query_param_table VALUES (1, 'Alice'), (2, 'Bob');")
    results = mem_db.execute_query("SELECT name FROM query_param_table WHERE id = ?;", (1,))
    assert results == [('Alice',)]

def test_execute_query_no_results(mem_db):
    mem_db.execute_script("CREATE TABLE empty_table (id INTEGER);")
    results = mem_db.execute_query("SELECT * FROM empty_table;")
    assert results == []

def test_execute_query_on_closed_connection(mem_db, capsys):
    mem_db.close()
    assert mem_db.execute_query("SELECT 1;") is None
    captured = capsys.readouterr()
    assert "Error: No database connection." in captured.out

def test_script_on_closed_connection(mem_db, capsys):
    mem_db.close()
    assert mem_db.execute_script("CREATE TABLE test (id INT);") is False
    captured = capsys.readouterr()
    assert "Error: No database connection." in captured.out

def test_table_exists_on_closed_connection(mem_db, capsys):
    mem_db.close()
    assert mem_db.table_exists("any_table") is False
    captured = capsys.readouterr()
    assert "Error: No database connection." in captured.out

def test_table_exists(mem_db):
    mem_db.execute_script("CREATE TABLE existing_table (id INT);")
    assert mem_db.table_exists("existing_table") is True
    assert mem_db.table_exists("non_existing_table") is False

def test_insert_dataframe_replace(mem_db):
    df1 = pd.DataFrame({'colA': [1, 2], 'colB': ['x', 'y']})
    assert mem_db.insert_dataframe(df1, "df_table", if_exists="replace") is True
    retrieved_df1 = mem_db.get_dataframe("SELECT * FROM df_table ORDER BY colA;")
    assert_frame_equal(retrieved_df1.reset_index(drop=True), df1.reset_index(drop=True), check_dtype=False)

    df2 = pd.DataFrame({'colA': [3, 4], 'colB': ['a', 'b']})
    assert mem_db.insert_dataframe(df2, "df_table", if_exists="replace") is True
    retrieved_df2 = mem_db.get_dataframe("SELECT * FROM df_table ORDER BY colA;")
    assert_frame_equal(retrieved_df2.reset_index(drop=True), df2.reset_index(drop=True), check_dtype=False)


def test_insert_dataframe_append(mem_db):
    df1 = pd.DataFrame({'id': [1], 'value': [10.0]})
    mem_db.insert_dataframe(df1, "append_test", if_exists="replace")

    df2 = pd.DataFrame({'id': [2], 'value': [20.0]})
    assert mem_db.insert_dataframe(df2, "append_test", if_exists="append") is True

    expected_df = pd.DataFrame({'id': [1, 2], 'value': [10.0, 20.0]})
    retrieved_df = mem_db.get_dataframe("SELECT * FROM append_test ORDER BY id;")
    retrieved_df['id'] = retrieved_df['id'].astype(int)
    expected_df['id'] = expected_df['id'].astype(int)
    assert_frame_equal(retrieved_df.sort_values('id').reset_index(drop=True),
                       expected_df.sort_values('id').reset_index(drop=True),
                       check_dtype=False)


def test_insert_dataframe_fail_if_exists(mem_db, capsys):
    df = pd.DataFrame({'data': [1]})
    mem_db.insert_dataframe(df, "fail_test", if_exists="replace")
    assert mem_db.insert_dataframe(df, "fail_test", if_exists="fail") is False
    captured = capsys.readouterr()
    assert "Error: Table fail_test already exists and if_exists='fail'." in captured.out
    assert mem_db.insert_dataframe(df, "new_fail_test", if_exists="fail") is True

def test_insert_dataframe_invalid_if_exists(mem_db, capsys):
    df = pd.DataFrame({'data': [1]})
    assert mem_db.insert_dataframe(df, "some_table", if_exists="invalid_option") is False
    captured = capsys.readouterr()
    assert "Error: Invalid value for if_exists: invalid_option" in captured.out

def test_insert_empty_dataframe_replace_not_exists(mem_db, capsys):
    empty_df = pd.DataFrame({'col1': pd.Series(dtype='int'), 'col2': pd.Series(dtype='str')})
    assert mem_db.insert_dataframe(empty_df, "empty_df_table_replace", if_exists="replace") is True
    captured = capsys.readouterr()
    assert "Warning: DataFrame is empty." in captured.out
    # Depending on strictness, an empty DF for "replace" might not create the table
    assert not mem_db.table_exists("empty_df_table_replace")

def test_insert_empty_dataframe_replace_exists(mem_db, capsys):
    mem_db.execute_script("CREATE TABLE empty_df_table_replace_e (col1 INT); INSERT INTO empty_df_table_replace_e VALUES(1);")
    empty_df = pd.DataFrame({'col1': pd.Series(dtype='int')}) # Ensure compatible schema for potential ops
    assert mem_db.insert_dataframe(empty_df, "empty_df_table_replace_e", if_exists="replace") is True
    captured = capsys.readouterr()
    assert "Warning: DataFrame is empty." in captured.out
    assert mem_db.table_exists("empty_df_table_replace_e") # Table should still exist
    assert mem_db.get_dataframe("SELECT * FROM empty_df_table_replace_e;").empty # But be empty

def test_insert_empty_dataframe_append(mem_db, capsys):
    mem_db.execute_script("CREATE TABLE empty_df_append_test (col1 INT);")
    empty_df = pd.DataFrame({'col1': pd.Series(dtype='int')})
    assert mem_db.insert_dataframe(empty_df, "empty_df_append_test", if_exists="append") is True
    captured = capsys.readouterr()
    assert "Warning: DataFrame is empty." in captured.out
    assert mem_db.get_dataframe("SELECT * FROM empty_df_append_test;").empty

def test_insert_not_a_dataframe(mem_db, capsys):
    not_df = [{"a":1}]
    assert mem_db.insert_dataframe(not_df, "not_df_table", "replace") is False
    captured = capsys.readouterr()
    assert "Error: Input is not a pandas DataFrame." in captured.out

def test_insert_dataframe_on_closed_connection(mem_db, capsys):
    df = pd.DataFrame({'a':[1]})
    mem_db.close()
    assert mem_db.insert_dataframe(df, "df_closed_conn", "replace") is False
    captured = capsys.readouterr()
    assert "Error: No database connection." in captured.out


def test_get_dataframe(mem_db):
    data = {'A': [1, 2, 3], 'B': ['apple', 'banana', 'cherry']}
    source_df = pd.DataFrame(data)
    mem_db.insert_dataframe(source_df, "source_table", if_exists="replace")

    retrieved_df = mem_db.get_dataframe("SELECT A, B FROM source_table ORDER BY A;")
    source_df['A'] = source_df['A'].astype('int64')
    retrieved_df['A'] = retrieved_df['A'].astype('int64')
    assert_frame_equal(retrieved_df.reset_index(drop=True), source_df[['A', 'B']].reset_index(drop=True))

def test_get_dataframe_empty_result(mem_db):
    mem_db.execute_script("CREATE TABLE no_data_table (id INT);")
    df = mem_db.get_dataframe("SELECT * FROM no_data_table WHERE id = 123;")
    assert df.empty

def test_get_dataframe_invalid_query(mem_db, capsys):
    df = mem_db.get_dataframe("SELECT FROMTABLE;")
    assert df.empty
    captured = capsys.readouterr()
    assert "Error executing query" in captured.out

def test_get_dataframe_on_closed_connection(mem_db, capsys):
    mem_db.close()
    assert mem_db.get_dataframe("SELECT 1;").empty
    captured = capsys.readouterr()
    assert "Error: No database connection." in captured.out

def test_connection_init_failure(tmp_path, monkeypatch, capsys):
    # Mock duckdb.connect to raise an exception
    def mock_connect(*args, **kwargs):
        raise RuntimeError("Mocked connection error")
    monkeypatch.setattr("duckdb.connect", mock_connect)

    with pytest.raises(RuntimeError, match="Mocked connection error"):
        DatabaseHandler(db_file=str(tmp_path / "fail_connect.duckdb"))

    # Also check that an error message was printed by our handler (if it gets that far)
    # In this case, the error is raised in _connect before print, so __init__ propagates it.
    # If _connect caught and printed, we'd check capsys.

def test_explicit_commit_effect_on_separate_connection(tmp_path):
    db_file = str(tmp_path / "commit_test.duckdb")
    with DatabaseHandler(db_file=db_file) as db1:
        db1.execute_script("CREATE TABLE commit_example (id INT);")
        db1.execute_script("INSERT INTO commit_example VALUES (1);")
    with DatabaseHandler(db_file=db_file) as db2:
        result = db2.execute_query("SELECT * FROM commit_example;")
        assert result == [(1,)]

def test_insert_dataframe_different_column_order(mem_db):
    df_original_order = pd.DataFrame({'id': [1], 'name': ['Alice']})
    df_different_order = pd.DataFrame({'name': ['Bob'], 'id': [2]})
    mem_db.execute_script("CREATE TABLE col_order_test (id INT, name TEXT);")
    assert mem_db.insert_dataframe(df_original_order, "col_order_test", if_exists="append")
    assert mem_db.insert_dataframe(df_different_order, "col_order_test", if_exists="append")
    retrieved_df = mem_db.get_dataframe("SELECT id, name FROM col_order_test ORDER BY id;")
    expected_df = pd.DataFrame({'id': [1, 2], 'name': ['Alice', 'Bob']})
    retrieved_df['id'] = retrieved_df['id'].astype('int64')
    expected_df['id'] = expected_df['id'].astype('int64')
    assert_frame_equal(retrieved_df.reset_index(drop=True), expected_df.reset_index(drop=True))
