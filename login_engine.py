# ═══════════════════════════════════════════════════════════════
# login_engine.py — WORKING LOGIN ENGINE
# ═══════════════════════════════════════════════════════════════

import asyncio, random, uuid, time, json
import httpx
from datetime import datetime
from google_play_scraper import app as play_scraper
from protobuf_decoder.protobuf_decoder import Parser
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from Pb2 import MajoRLoGinrEs_pb2, PorTs_pb2

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV  = b'6oyZDr22E3ychjM%'

REGION_LANG = {
    "BD": "bn", "IND": "hi", "PK": "ur", "SG": "en", "ID": "id",
    "ME": "ar", "VN": "vi", "TW": "zh", "CIS": "ru", "TH": "th",
    "EU": "en", "US": "en", "SAC": "es", "LK": "en", "BR": "pt",
}

DEFAULT_HEADERS = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue', 'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859', 'X-GA': 'v1 1', 'ReleaseVersion': 'OB55'
}


def _pb_varint(n):
    if n < 0: n = (1 << 64) + n
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)


def _pb_tag(f, w): return _pb_varint((f << 3) | w)


def _pb_field(f, v):
    if isinstance(v, bool): v = int(v)
    if isinstance(v, int): return _pb_tag(f, 0) + _pb_varint(v)
    if isinstance(v, str):
        d = v.encode('utf-8')
        return _pb_tag(f, 2) + _pb_varint(len(d)) + d
    if isinstance(v, (bytes, bytearray)):
        d = bytes(v)
        return _pb_tag(f, 2) + _pb_varint(len(d)) + d
    return b""


async def aes_encrypt(payload, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(payload, AES.block_size))


def get_proto_field(d, key, default=None):
    if not d or not isinstance(d, dict): return default
    if key in d: return d[key].get('data', default)
    if str(key) in d: return d[str(key)].get('data', default)
    return default


async def parse_results(parsed_results):
    result_dict = {}
    for result in parsed_results:
        field_data = {"wire_type": result.wire_type}
        if result.wire_type in ("varint", "string", "bytes"):
            field_data["data"] = result.data
        elif result.wire_type == "length_delimited":
            if hasattr(result.data, "results"):
                field_data["data"] = await parse_results(result.data.results)
            elif isinstance(result.data, list):
                field_data["data"] = await parse_results(result.data)
            else:
                field_data["data"] = str(result.data)
        result_dict[str(result.field)] = field_data
    return result_dict


async def get_playstore_version():
    loop = asyncio.get_event_loop()
    try:
        res = await loop.run_in_executor(
            None, lambda: play_scraper('com.dts.freefireth', lang='hi', country='id'))
        return res.get("version", "1.132.8")
    except Exception:
        return "1.132.8"


async def version_config(client_http, region="IND"):
    app_version = await get_playstore_version()
    lang = REGION_LANG.get(region, "hi")
    api_url = (f"https://version.ggwhitehawk.com/live/ver.php?version={app_version}"
               f"&lang={lang}&device=android&channel=android&appstore=googleplay"
               f"&region={region}&whitelist_version=1.3.0&whitelist_sp_version=1.0.0")
    try:
        res = await client_http.get(api_url)
        data = res.json()
        return data.get("latest_release_version"), data.get("remote_version"), data.get("server_url")
    except Exception:
        return None


async def get_access_token(client_http, uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {
        "Host": "100067.connect.garena.com",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B Build/SP1A.210812.016)",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate, br", "Connection": "close"
    }
    data = {
        "uid": str(uid), "password": str(password), "response_type": "token",
        "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    for _ in range(3):
        try:
            r = await client_http.post(url, headers=hdrs, data=data)
            if r.status_code == 200:
                rd = r.json()
                return rd.get("open_id"), rd.get("access_token"), rd.get("platform", 4)
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def build_majorlogin_payload(open_id, access_token, platform, client_version):
    try:
        proto = MajoRLoGinrEq_pb2.MajorLogin() if False else None
        from Pb2 import MajoRLoGinrEq_pb2 as ML
        proto = ML.MajorLogin()
        proto.event_time = str(datetime.now())[:-7]
        proto.game_name = "free fire"
        proto.platform_id = 1
        proto.client_version = str(client_version)
        proto.client_version_code = "2019121229"
        proto.system_software = "Android OS 15 / API-35 (AP3A.240905.015.A2/185014)"
        proto.system_hardware = "Handheld"
        proto.device_type = "Handheld"
        proto.screen_width = 1600
        proto.screen_height = 719
        proto.screen_dpi = "234"
        proto.processor_details = "ARM64 FP ASIMD AES | 1820 | 8"
        proto.memory = 2798
        proto.gpu_renderer = "Mali-G57"
        proto.gpu_version = "OpenGL ES 3.2 v1.r49p1-04eac0.2848c17a2fd4e9340e06555168eaa3c9"
        proto.unique_device_id = f"Google|{str(uuid.uuid4())}"
        proto.client_ip = f"{random.randint(103,223)}.{random.randint(10,250)}.{random.randint(10,250)}.{random.randint(10,250)}"
        proto.language = "hi"
        proto.open_id = str(open_id)
        proto.open_id_type = "4"
        proto.login_open_id_type = 4
        proto.access_token = str(access_token)
        proto.login_by = 2
        proto.platform_sdk_id = 1
        proto.origin_platform_type = "4"
        proto.primary_platform_type = "4"
        proto.reg_avatar = 1
        proto.channel_type = 3
        proto.telecom_operator = "JIO"
        proto.network_operator_a = "JIO"
        proto.network_type = "WIFI"
        proto.network_type_a = "WIFI"
        proto.cpu_type = 2
        proto.cpu_architecture = "64"
        proto.graphics_api = "OpenGLES2"
        proto.supported_astc_bitset = 8191
        proto.client_using_version = "7428b253defc164018c604a1ebbfebdf"
        proto.loading_time = 15078
        proto.release_channel = "android"
        proto.extra_info = "KqsHTx3+QOmBRR1WKvaWewlcpqJBfjki+PPHQoQG8+0yV+Uos7gUFFjHMQ/e7u6han6Fl77r7c3vMN3p8UbKgN+nfycQCgwBmWgBzomx2gj84c+p"
        proto.android_engine_init_flag = 111207
        proto.if_push = 1
        proto.is_vpn = 0
        proto.memory_available.version = 55
        proto.memory_available.hidden_value = 81
        proto.external_storage_total = 49973
        proto.external_storage_available = 11338
        proto.internal_storage_total = 854
        proto.internal_storage_available = 11466
        proto.game_disk_storage_total = 49973
        proto.game_disk_storage_available = 11466
        proto.external_sdcard_total_storage = 49973
        proto.external_sdcard_avail_storage = 11466
        proto.library_path = "/data/app/~~lHFxTCCbupG2QVJmsURtZw==/com.dts.freefireth-N3aCHpHNXpdxjD80uIIbww==/lib/arm64"
        proto.library_token = "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~lHFxTCCbupG2QVJmsURtZw==/com.dts.freefireth-N3aCHpHNXpdxjD80uIIbww==/base.apk"
        base_payload = proto.SerializeToString()
        extra = b""
        extra += _pb_field(96, '{"cur_rate":[90,60,120],"support_etc2":false}')
        extra += _pb_field(97, 1)
        extra += _pb_field(99, "4")
        extra += _pb_field(100, "4")
        extra += _pb_field(102, b"\x17]ENWU\x0eR5")
        extra += _pb_field(104, 52882)
        extra += _pb_field(105, 1)
        extra += _pb_field(106, "https://dl.ak.freefiremobile.com/live/ABHotUpdates/|https://core-ak.freefiremobile.com/live/ABHotUpdates/|6b2078db9d22dd98f8e9386a39af8462")
        extra += _pb_field(107, "c8e41b7a93f02d56e1a94c7b8203f5d1")
        return await aes_encrypt(base_payload + extra, AES_KEY, AES_IV)
    except Exception as e:
        print(f"[BUILD ERROR] {e}")
        return None


async def send_majorlogin(client_http, data, release_version, server_url):
    try:
        url = f"{server_url}MajorLogin" if server_url.endswith('/') else f"{server_url}/MajorLogin"
        req_headers = DEFAULT_HEADERS.copy()
        req_headers["ReleaseVersion"] = str(release_version)
        response = await client_http.post(url, headers=req_headers, data=data)
        if response.status_code != 200 or len(response.content) < 40:
            return None
        res_proto = MajoRLoGinrEs_pb2.MajorLoginRes()
        try: res_proto.ParseFromString(response.content)
        except Exception: pass
        if not (res_proto.account_uid and res_proto.token):
            for offset in range(min(128, len(response.content))):
                try:
                    c = MajoRLoGinrEs_pb2.MajorLoginRes()
                    c.ParseFromString(response.content[offset:])
                    if c.account_uid and c.token: res_proto = c; break
                except Exception: pass
        parsed = Parser().parse(response.content.hex())
        dict_res = await parse_results(parsed)
        key_val = get_proto_field(dict_res, 22) or res_proto.key or AES_KEY
        iv_val = get_proto_field(dict_res, 23) or res_proto.iv or AES_IV
        if isinstance(key_val, str):
            try: key_val = bytes.fromhex(key_val)
            except Exception: pass
        if isinstance(iv_val, str):
            try: iv_val = bytes.fromhex(iv_val)
            except Exception: pass
        res_proto.key = key_val
        res_proto.iv = iv_val
        if not res_proto.url: res_proto.url = server_url
        return res_proto
    except Exception as e:
        print(f"[MAJORLOGIN ERROR] {e}")
        return None


async def send_getlogin(client_http, data, base_url, token, release_version):
    try:
        url = f"{base_url.rstrip('/')}/GetLoginData"
        req_headers = DEFAULT_HEADERS.copy()
        req_headers["ReleaseVersion"] = str(release_version)
        req_headers['Authorization'] = f"Bearer {token}"
        response = await client_http.post(url, headers=req_headers, data=data)
        if response.status_code != 200: return None
        res_proto = PorTs_pb2.GetLoginData()
        try: res_proto.ParseFromString(response.content)
        except Exception: pass
        parsed = Parser().parse(response.content.hex())
        dict_res = await parse_results(parsed)
        return res_proto, dict_res
    except Exception as e:
        print(f"[GETLOGIN ERROR] {e}")
        return None


async def do_login(uid, password, region_pref="IND"):
    async with httpx.AsyncClient(verify=False, timeout=15.0) as client_http:
        print("[*] Fetching version config...")
        vc = await version_config(client_http, region_pref)
        if not vc:
            print("[-] Version config error"); return None
        release_version, client_version, server_url = vc
        print(f"[+] Version: {client_version} | Release: {release_version}")

        print("[*] Getting Access Token...")
        tg = await get_access_token(client_http, uid, password)
        if not tg:
            print("[-] Invalid credentials"); return None
        open_id, access_token, platform = tg

        print("[*] Building MajorLogin payload...")
        payload = await build_majorlogin_payload(open_id, access_token, platform, client_version)
        if not payload:
            print("[-] Build failed"); return None

        major_res = await send_majorlogin(client_http, payload, release_version, server_url)
        if not major_res or not major_res.account_uid:
            print("[-] MajorLogin rejected"); return None

        print("[*] Getting Login Data...")
        gl = await send_getlogin(client_http, payload, major_res.url, major_res.token, release_version)
        if not gl:
            print("[-] GetLoginData failed"); return None
        res_proto, dict_res = gl

        online_addr = res_proto.Online_IP_Port or get_proto_field(dict_res, 14, "0.0.0.0:0")
        if ':' in online_addr:
            online_ip, online_port = online_addr.split(":")
        else:
            online_ip, online_port = "0.0.0.0", "0"

        chat_addr = res_proto.AccountIP_Port or get_proto_field(dict_res, 32, online_addr)
        if ':' in chat_addr:
            chat_ip, chat_port = chat_addr.split(":")
        else:
            chat_ip, chat_port = online_ip, online_port

        return {
            "bot_uid":      str(major_res.account_uid),
            "token":        major_res.token,
            "region":       major_res.region or region_pref,
            "key":          major_res.key or AES_KEY,
            "iv":           major_res.iv or AES_IV,
            "timestamp":    major_res.timestamp or int(time.time()),
            "online_ip":    online_ip,
            "online_port":  online_port,
            "chat_ip":      chat_ip,
            "chat_port":    chat_port,
            "account_name": res_proto.AccountName or "Unknown",
            "clan_id":      res_proto.Clan_ID or 0,
            "clan_data":    res_proto.Clan_Compiled_Data or "",
            "server_url":   major_res.url,
        }