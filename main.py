import base64
import logging
import os
import pickle
import threading
import time
from datetime import datetime
from mongo import *
from flask import Flask, jsonify, request, send_file, abort
from bson.objectid import ObjectId
from config import *
from process_assets import scan_dir, process_image, process_video, process_text, match_text_and_image, match_batch
from utils import get_file_hash, get_string_hash, softmax

logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)


is_scanning = False
scan_thread = None
scan_start_time = 0
scanning_files = 0
total_images = 0
total_video_frames = 0
scanned_files = 0
is_continue_scan = False

def init():
    """初始化"""
    global total_images, total_video_frames, is_scanning, scan_thread

    total_images = image_collection.count_documents({})
    total_video_frames = video_collection.count_documents({})

    if AUTO_SCAN:
        is_scanning = True
        scan()

def clean_cache():
    """
    清空搜索缓存
    :return:
    """
    with app.app_context():
        db.session.query(Cache).delete()
        db.session.commit()


def scan():
    global is_scanning, total_images, total_video_frames, scanning_files, scanned_files, scan_start_time, is_continue_scan
    logger.info("开始扫描")
    scan_start_time = time.time()
    start_time = time.time()

    if os.path.isfile("assets.pickle"):
        logger.info("读取上次的目录缓存")
        is_continue_scan = True
        with open("assets.pickle", "rb") as f:
            assets = pickle.load(f)
        for asset in assets.copy():
            if asset.startswith(SKIP_PATH):
                assets.remove(asset)
    else:
        is_continue_scan = False
        assets = scan_dir(ASSETS_PATH, SKIP_PATH, IMAGE_EXTENSIONS + VIDEO_EXTENSIONS)
        with open("assets.pickle", "wb") as f:
            pickle.dump(assets, f)
    scanning_files = len(assets)

    # 删除不存在的文件记录
    for file in image_collection.find():
        if not is_continue_scan and (file['path'] not in assets or file['path'].startswith(SKIP_PATH)):
            logger.info(f"文件已删除：{file['path']}")
            image_collection.delete_one({"_id": file["_id"]})


    # 扫描文件
    for asset in assets.copy():
        scanned_files += 1
        if scanned_files % 100 == 0:  # 每扫描100次重新save一下
            with open("assets.pickle", "wb") as f:
                pickle.dump(assets, f)
            # 如果文件不存在，则忽略（扫描时文件被移动或删除则会触发这种情况）
        if not os.path.isfile(asset):
            continue
        # 如果数据库里有这个文件，并且修改时间一致，则跳过，否则进行预处理并入库


        if asset.lower().endswith(IMAGE_EXTENSIONS):  # 扫描图片
            db_record = image_collection.find_one({"path": asset})
            modify_time = datetime.fromtimestamp(os.path.getmtime(asset))
            if db_record and db_record['modify_time'] == modify_time:
                logger.debug(f"文件无变更，跳过：{asset}")
                assets.remove(asset)
                continue
            features = process_image(asset)
            if features is None:
                assets.remove(asset)
                continue
            # 写入数据库
            features = pickle.dumps(features)
            if db_record:
                logger.info(f"文件有更新：{asset}")
                db_record['modify_time'] = modify_time
                db_record['features'] = features
            else:
                logger.info(f"新增文件：{asset}")
                image_data = {
                    "path": asset,
                    "modify_time": modify_time,
                    "features": features
                }
                image_collection.insert_one(image_data)
                total_images = image_collection.count_documents({})  # 获取文件总数
        else:#扫描视频
            db_record=video_collection.find_one({'path':asset})
            modify_time = datetime.fromtimestamp(os.path.getmtime(asset))
            if db_record and db_record['modify_time'] == modify_time:
                logger.debug(f"文件无变更，跳过：{asset}")
                assets.remove(asset)
                continue
            # 写入数据库
            if db_record:
                logger.info(f"文件有更新：{asset}")
                video_collection.delete_one({"_id": file["_id"]})  # 视频文件直接删了重新写数据，而不是直接替换，因为视频长短可能有变化，不方便处理
            else:
                logger.info(f"新增文件：{asset}")
            for frame_time, features in process_video(asset):
                video_data={
                    "path": asset,
                    "frame_time":frame_time,
                    "modify_time": modify_time,
                    "features": pickle.dumps(features)
                }
                video_collection.insert_one(video_data)
                total_video_frames = video_collection.count_documents({})  # 获取文件总数

            assets.remove(asset)
    scanning_files = 0
    scanned_files = 0
    os.remove("assets.pickle")
    logger.info("扫描完成，用时%d秒" % int(time.time() - start_time))

    is_scanning = False


def search_image(positive_prompt="", negative_prompt="", img_path="",
                 positive_threshold=POSITIVE_THRESHOLD, negative_threshold=NEGATIVE_THRESHOLD, image_threshold=IMAGE_THRESHOLD):
    """
    搜图
    :param positive_prompt: 正向提示词
    :param negative_prompt: 反向提示词
    :param img_path: 图片路径，如果存在，说明是用图搜索，此时忽略提示词
    :param positive_threshold: 文字搜索阈值，高于此分数才显示
    :param negative_threshold: 文字过滤阈值，低于此分数才显示
    :param image_threshold: 以图搜素材匹配阈值，高于这个分数才展示
    :return:
    """
    if img_path:
        positive_feature = process_image(img_path)
        positive_threshold = image_threshold
        negative_feature = None
    else:
        positive_feature = process_text(positive_prompt)
        negative_feature = process_text(negative_prompt)
    scores_list = []
    t0 = time.time()

    image_features = []
    file_list = []
    for file in image_collection.find():
        features = pickle.loads(file['features'])
        if features is None:  # 内容损坏，删除该条记录
            image_collection.delete_one({"_id": file["_id"]})
            continue
        file_list.append(file)
        image_features.append(features)
    scores = match_batch(positive_feature, negative_feature, image_features, positive_threshold, negative_threshold)
    result=[]
    for i in range(len(file_list)):
        if not scores[i]:
            continue
        # scores_list.append({"url": "api/get_image/%d" % file_list[i]['_id'], "path": file_list[i]['path'], "score": float(scores[i])})
        print(file_list[i]["_id"])
        logger.debug(file_list[i]["path"])
        result.append({
            "url": "api/get_image/%s" % str(file_list[i]["_id"]),#"api/get_image/%d" % file_list[i]["_id"]期待得到数字id，但是mongo返回的是ObjectID，只能转化为str，导致结果图片显示错误
            "path": file_list[i]["path"],
            "score": float(scores[i])
            })
        scores_list.extend(result)
    logger.info("查询使用时间：%.2f" % (time.time() - t0))
    sorted_list = sorted(scores_list, key=lambda x: x["score"], reverse=True)
    return sorted_list

def search_video(positive_prompt="", negative_prompt="", img_path="",
                 positive_threshold=POSITIVE_THRESHOLD, negative_threshold=NEGATIVE_THRESHOLD, image_threshold=IMAGE_THRESHOLD):
    """
    搜视频
    :param positive_prompt: 正向提示词
    :param negative_prompt: 反向提示词
    :param img_path: 图片路径，如果存在，说明是用图搜索，此时忽略提示词
    :param positive_threshold: 文字搜索阈值，高于此分数才显示
    :param negative_threshold: 文字过滤阈值，低于此分数才显示
    :param image_threshold: 以图搜素材匹配阈值，高于这个分数才展示
    :return:
    """
    if img_path:
        positive_feature = process_image(img_path)
        positive_threshold = image_threshold
        negative_feature = None
    else:
        positive_feature = process_text(positive_prompt)
        negative_feature = process_text(negative_prompt)
    scores_list = []
    t0 = time.time()

    for path in video_collection.distinct('path'):  # 逐个视频比对
        # path = path[0]
        frames = list(video_collection.find({"path": path}).sort("frame_time", 1))
        image_features = list(map(lambda x: pickle.loads(x['features']), frames))
        scores = match_batch(positive_feature, negative_feature, image_features, positive_threshold, negative_threshold)
        index_pairs = get_index_pairs(scores)
        for index_pair in index_pairs:
            # 间隔小于等于2倍FRAME_INTERVAL的算为同一个素材，同时开始时间和结束时间各延长0.5个FRAME_INTERVAL
            score = max(scores[index_pair[0]:index_pair[1] + 1])
            if index_pair[0] > 0:
                start_time = int((frames[index_pair[0]]['frame_time'] + frames[index_pair[0] - 1]['frame_time']) / 2)
            else:
                start_time = frames[index_pair[0]]['frame_time']
            if index_pair[1] < len(scores) - 1:
                end_time = int((frames[index_pair[1]]['frame_time'] + frames[index_pair[1] + 1]['frame_time']) / 2 + 0.5)
            else:
                end_time = frames[index_pair[1]]['frame_time']
            scores_list.append(
                {"url": "api/get_video/%s" % base64.urlsafe_b64encode(path.encode()).decode() + "#t=%.1f,%.1f" % (
                    start_time, end_time),
                # {"url": "api/get_video/%s" % path + "#t=%.1f,%.1f" % (
                #     start_time, end_time),
                 "path": path, "score": score, "start_time": start_time, "end_time": end_time})
    logger.info("查询使用时间：%.2f" % (time.time() - t0))
    sorted_list = sorted(scores_list, key=lambda x: x["score"], reverse=True)
    return sorted_list


def get_index_pairs(scores):
    """返回连续的帧序号，如第2-5帧、第11-13帧都符合搜索内容，则返回[(2,5),(11,13)]"""
    indexes = []
    for i in range(len(scores)):
        if scores[i]:
            indexes.append(i)
    result = []
    start_index = -1
    for i in range(len(indexes)):
        if start_index == -1:
            start_index = indexes[i]
        elif indexes[i] - indexes[i - 1] > 2:  # 允许中间空1帧
            result.append((start_index, indexes[i - 1]))
            start_index = indexes[i]
    if start_index != -1:
        result.append((start_index, indexes[-1]))
    return result


@app.route("/", methods=["GET"])
def index_page():
    """主页"""
    return app.send_static_file("index.html")


@app.route("/api/scan", methods=["GET"])
def api_scan():
    """开始扫描"""
    global is_scanning, scan_thread
    if not is_scanning:
        is_scanning = True
        scan_thread = threading.Thread(target=scan, args=())
        scan_thread.start()
        return jsonify({"status": "start scanning"})
    return jsonify({"status": "already scanning"})


@app.route("/api/status", methods=["GET"])
def api_status():
    """状态"""
    global is_scanning, scanning_files, scanned_files, scan_start_time, total_images, total_video_frames
    if scanned_files:
        remain_time = (time.time() - scan_start_time) / scanned_files * scanning_files
    else:
        remain_time = 0
    if is_scanning and scanning_files != 0:
        progress = scanned_files / scanning_files
    else:
        progress = 0
    return jsonify({"status": is_scanning, "total_images": total_images, "total_video_frames": total_video_frames, "scanning_files": scanning_files,
                    "remain_files": scanning_files - scanned_files, "progress": progress, "remain_time": int(remain_time),
                    "enable_cache": ENABLE_CACHE})


# @app.route("/api/clean_cache", methods=["GET", "POST"])
# def api_clean_cache():
#     clean_cache()
#     return "OK"


@app.route("/api/match", methods=["POST"])
def api_match():
    """
    匹配文字对应的素材
    curl -X POST -H "Content-Type: application/json" -d '{"positive": "openai","negative": "","top_n": "6","search_type": 0,"positive_threshold": 10,"negative_threshold": 10,"image_threshold": 85}' http://localhost:8085/api/match
    """
    data = request.get_json()
    top_n = int(data['top_n'])
    search_type = data['search_type']
    positive_threshold = data['positive_threshold']
    negative_threshold = data['negative_threshold']
    image_threshold = data['image_threshold']
    logger.debug(data)
    # 计算hash
    if search_type == 0:  # 以文搜图
        _hash = get_string_hash(
            "以文搜图%d,%d\npositive: %r\nnegative: %r" % (positive_threshold, negative_threshold, data['positive'], data['negative']))
    elif search_type == 1:  # 以图搜图
        _hash = get_string_hash("以图搜图%d,%s" % (image_threshold, get_file_hash(UPLOAD_TMP_FILE)))
    elif search_type == 2:  # 以文搜视频
        _hash = get_string_hash(
            "以文搜视频%d,%d\npositive: %r\nnegative: %r" % (positive_threshold, negative_threshold, data['positive'], data['negative']))
    elif search_type == 3:  # 以图搜视频
        _hash = get_string_hash("以图搜视频%d,%s" % (image_threshold, get_file_hash(UPLOAD_TMP_FILE)))
    elif search_type == 4:  # 图文比对
        _hash1 = get_string_hash("text: %r" % data['text'])
        _hash2 = get_file_hash(UPLOAD_TMP_FILE)
        _hash = get_string_hash("图文比对\nhash1: %r\nhash2: %r" % (_hash1, _hash2))
    else:
        logger.warning(f"search_type不正确：{search_type}")
        abort(500)
    # 查找cache
    if ENABLE_CACHE:
        if search_type == 0 or search_type == 1 or search_type == 2 or search_type == 3:

            # sorted_list = db.session.query(Cache).filter_by(id=_hash).first()
            sorted_list = cache_collection.find_one({'_id':_hash})

            if sorted_list:
                sorted_list = pickle.loads(sorted_list['result'])
                logger.debug(f"命中缓存：{_hash}")
                sorted_list = sorted_list[:top_n]
                scores = [item["score"] for item in sorted_list]
                softmax_scores = softmax(scores)
                if search_type == 0 or search_type == 1:
                    new_sorted_list = [{
                        "url": item["url"], "path": item["path"], "score": "%.2f" % (item["score"] * 100),
                        "softmax_score": "%.2f%%" % (score * 100)
                    } for item, score in zip(sorted_list, softmax_scores)]
                elif search_type == 2 or search_type == 3:
                    new_sorted_list = [{
                        "url": item["url"], "path": item["path"], "score": "%.2f" % (item["score"] * 100),
                        "softmax_score": "%.2f%%" % (score * 100), "start_time": item["start_time"], "end_time": item["end_time"]
                    } for item, score in zip(sorted_list, softmax_scores)]
                return jsonify(new_sorted_list)
    # 如果没有cache，进行匹配并写入cache
    if search_type == 0:
        sorted_list = search_image(positive_prompt=data['positive'], negative_prompt=data['negative'],
                                   positive_threshold=positive_threshold, negative_threshold=positive_threshold)[:MAX_RESULT_NUM]
    elif search_type == 1:
        sorted_list = search_image(img_path=UPLOAD_TMP_FILE, image_threshold=image_threshold)[:MAX_RESULT_NUM]
    elif search_type == 2:
        sorted_list = search_video(positive_prompt=data['positive'], negative_prompt=data['negative'],
                                   positive_threshold=positive_threshold, negative_threshold=positive_threshold)[:MAX_RESULT_NUM]
    elif search_type == 3:
        sorted_list = search_video(img_path=UPLOAD_TMP_FILE, image_threshold=image_threshold)[:MAX_RESULT_NUM]
    elif search_type == 4:
        return jsonify({"score": "%.2f" % (match_text_and_image(process_text(data['text']), process_image(UPLOAD_TMP_FILE)) * 100)})
    # 写入缓存
    if ENABLE_CACHE:
        with app.app_context():
            cache_document = {
                "_id": _hash,
                "result": pickle.dumps(sorted_list)
            }
            cache_collection.insert_one(cache_document)

    sorted_list = sorted_list[:top_n]
    scores = [item["score"] for item in sorted_list]
    softmax_scores = softmax(scores)
    if search_type == 0 or search_type == 1:
        new_sorted_list = [{
            "url": item["url"], "path": item["path"], "score": "%.2f" % (item["score"] * 100), "softmax_score": "%.2f%%" % (score * 100)
        } for item, score in zip(sorted_list, softmax_scores)]
    elif search_type == 2 or search_type == 3:
        new_sorted_list = [{
            "url": item["url"], "path": item["path"], "score": "%.2f" % (item["score"] * 100), "softmax_score": "%.2f%%" % (score * 100),
            "start_time": item["start_time"], "end_time": item["end_time"]
        } for item, score in zip(sorted_list, softmax_scores)]
    return jsonify(new_sorted_list)


@app.route('/api/get_image/<image_id>', methods=['GET'])
def api_get_image(image_id):
    """
    通过image_path获取文件
    """
    logger.debug(type(image_id))
    file = image_collection.find_one({'_id':ObjectId(image_id)})
    logger.debug(file)

    return send_file(file['path'])


@app.route('/api/get_video/<video_path>', methods=['GET'])
def api_get_video(video_path):
    """
    通过video_path获取文件
    """
    path = base64.b64decode(video_path).decode('utf-8')
    logger.debug(path)

    video = video_collection.find_one({'path':path})
    if not video:  # 如果路径不在数据库中，则返回404，防止任意文件读取攻击
        abort(404)
    return send_file(path)


@app.route('/api/upload', methods=['POST'])
def api_upload():
    logger.debug(request.files)
    f = request.files['file']
    f.save(UPLOAD_TMP_FILE)
    return 'file uploaded successfully'


if __name__ == '__main__':
    init()
    app.run(port=8085, host="0.0.0.0")
