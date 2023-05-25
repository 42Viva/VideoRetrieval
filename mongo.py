from pymongo import MongoClient

client = MongoClient('mongodb://localhost:27017/')
db = client['data']

class Image:
    def __init__(self, path, modify_time, features):
        self.path = path
        self.modify_time = modify_time
        self.features = features

class Video:
    def __init__(self, path, frame_time, modify_time, features):
        self.path = path
        self.frame_time = frame_time
        self.modify_time = modify_time
        self.features = features

class Cache:
    def __init__(self, id, result):
        self.id = id
        self.result = result

def insert_image(path, modify_time, features):
    image = Image(path, modify_time, features)
    image_collection.insert_one(image.__dict__)

def insert_video(path, frame_time, modify_time, features):
    video = Video(path, frame_time, modify_time, features)
    video_collection.insert_one(video.__dict__)

def insert_cache(id, result):
    cache = Cache(id, result)
    cache_collection.insert_one(cache.__dict__)

def get_all_images():
    images = []
    for image in image_collection.find():
        images.append(image)
    return images

def get_all_videos():
    videos = []
    for video in video_collection.find():
        videos.append(video)
    return videos

def get_cache(id):
    return cache_collection.find_one({'id': id})

image_collection = db['images']
video_collection = db['videos']
cache_collection = db['cache']