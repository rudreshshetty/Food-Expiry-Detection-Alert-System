from dotenv import load_dotenv
load_dotenv()
from app import app

with app.test_client() as client:
    res = client.post('/login', data={'username': 'testuser', 'password': 'Test@1234'}, follow_redirects=True)
    print('login status', res.status_code)
    print('login contains invalid', b'Invalid username or password' in res.data)
    res = client.post('/add', data={'name':'TESTITEM','expiry_date':'2026-12-31','days_since_purchase':'1','category':'Grocery','storage_temp':'5'}, follow_redirects=True)
    print('add status', res.status_code)
    print('add redirected', res.request.path)
    print('add flash snippet', res.data[:200])

    # Inspect Mongo
    from app import get_db_connection, DB_DRIVER
    conn = get_db_connection()
    print('DB_DRIVER', DB_DRIVER)
    if DB_DRIVER == 'mongo':
        count = conn.items.count_documents({'name': 'TESTITEM'})
        print('items_count', count)
        conn.items.delete_many({'name': 'TESTITEM'})
    else:
        print('not mongo', DB_DRIVER)
