from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017')
db = client['food_expiry']
print('collections', db.list_collection_names())
print('items_count', db.items.count_documents({}))
print('settings_count', db.settings.count_documents({}))
print('users_count', db.users.count_documents({}))
