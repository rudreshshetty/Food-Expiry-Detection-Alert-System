from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017', serverSelectionTimeoutMS=3000)
print('server', client.server_info()['version'])
db = client['food_expiry']
print('users', db.users.count_documents({}))
print('items', db.items.count_documents({}))
print('sample user', db.users.find_one())
print('sample item', db.items.find_one())
