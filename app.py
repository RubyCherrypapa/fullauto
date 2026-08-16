import hmac
import hashlib
import urllib.parse
import time
from datetime import datetime
import requests
import streamlit as st
import google.generativeai as genai

# ==========================================
# 페이지 설정
# ==========================================
st.set_page_config(
    page_title="쿠팡 → Threads 자동 홍보 (Gemini)",
    page_icon="🚀",
    layout="centered"
)

st.title("🚀 쿠팡 상품 → Threads 자동 홍보")
st.write("쿠팡 상품을 검색하고, Gemini가 스레드 홍보글을 만들고, 바로 Threads 계정에 업로드까지 한 번에!")

# ==========================================
# 🔑 secrets.toml에서 기본값 불러오기 (있으면 자동입력, 없으면 빈칸)
# ==========================================
def get_secret(key, default=""):
    """st.secrets에 값이 있으면 반환하고, 없으면 default를 반환합니다."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        # secrets.toml 파일 자체가 없는 경우에도 에러 없이 넘어가도록 처리
        return default


DEFAULT_COUPANG_ACCESS = get_secret("COUPANG_ACCESS_KEY")
DEFAULT_COUPANG_SECRET = get_secret("COUPANG_SECRET_KEY")
DEFAULT_GEMINI_KEY = get_secret("GEMINI_API_KEY")

# Threads 계정을 최대 3개까지 지원 (THREADS_USER_ID_1 / THREADS_ACCESS_TOKEN_1 ... _3)
THREADS_ACCOUNT_COUNT = 3
DEFAULT_THREADS_ACCOUNTS = [
    {
        "user_id": get_secret(f"THREADS_USER_ID_{i}"),
        "token": get_secret(f"THREADS_ACCESS_TOKEN_{i}"),
    }
    for i in range(1, THREADS_ACCOUNT_COUNT + 1)
]

# ==========================================
# 🔑 API 키 입력 (secrets.toml에 값이 있으면 자동으로 채워짐)
# ==========================================
st.sidebar.header("🔑 API 설정")

with st.sidebar.expander("① 쿠팡 파트너스 API", expanded=True):
    coupang_access = st.text_input(
        "Coupang Access Key", value=DEFAULT_COUPANG_ACCESS, type="password"
    )
    coupang_secret = st.text_input(
        "Coupang Secret Key", value=DEFAULT_COUPANG_SECRET, type="password"
    )

with st.sidebar.expander("② Gemini API", expanded=True):
    gemini_key = st.text_input(
        "Gemini API Key", value=DEFAULT_GEMINI_KEY, type="password"
    )
    gemini_model = st.selectbox(
        "모델 선택 (하나가 429면 다른 모델로 전환해보세요)",
        options=[
            "gemini-flash-latest",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-2.0-flash",
        ],
        index=0,
        help="무료 티어의 요청 한도는 모델별로 따로 부여됩니다. "
             "한 모델에서 429(quota exceeded)가 뜨면 다른 모델로 바꿔서 계속 사용할 수 있어요."
    )
    st.caption("현재 사용량 확인: https://ai.dev/rate-limit")

with st.sidebar.expander("③ Threads API (자동 업로드용, 최대 3계정)", expanded=True):
    st.caption(
        "Meta for Developers에서 Threads API 앱을 등록하고, "
        "threads_basic / threads_content_publish 권한으로 발급받은 "
        "Access Token과 본인의 Threads User ID를 계정별로 입력하세요. "
        "동시에 업로드하고 싶은 계정만 체크하면 됩니다."
    )
    threads_accounts = []
    for i in range(THREADS_ACCOUNT_COUNT):
        st.markdown(f"**계정 {i + 1}**")
        use_account = st.checkbox(
            f"계정 {i + 1} 업로드에 사용",
            value=bool(DEFAULT_THREADS_ACCOUNTS[i]["user_id"]),
            key=f"threads_use_{i}",
        )
        user_id = st.text_input(
            f"Threads User ID {i + 1}",
            value=DEFAULT_THREADS_ACCOUNTS[i]["user_id"],
            key=f"threads_uid_{i}",
        )
        token = st.text_input(
            f"Threads Access Token {i + 1}",
            value=DEFAULT_THREADS_ACCOUNTS[i]["token"],
            type="password",
            key=f"threads_token_{i}",
        )
        threads_accounts.append({
            "label": f"계정 {i + 1}",
            "user_id": user_id,
            "token": token,
            "use": use_account,
        })

st.sidebar.info(
    "secrets.toml에 값을 저장해두면 자동으로 채워집니다. "
    "직접 입력한 값은 이 브라우저 세션에만 유지되며 서버에 별도 저장되지 않습니다."
)

# 상태 관리 초기화
if 'products' not in st.session_state:
    st.session_state.products = []
if 'generated_posts' not in st.session_state:
    st.session_state.generated_posts = {}

# ==========================================
# 1. 쿠팡 상품 검색
# ==========================================
def search_coupang_products(keyword, access_key, secret_key):
    """쿠팡 파트너스 API를 통해 상품을 검색합니다."""
    method = "GET"
    path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"

    encoded_keyword = urllib.parse.quote(keyword)
    query = f"keyword={encoded_keyword}&limit=5"

    # 쿠팡 API 서명은 반드시 UTC 시간을 사용
    datetime_utc = datetime.utcnow()
    formatted_date = datetime_utc.strftime('%y%m%d') + 'T' + datetime_utc.strftime('%H%M%S') + 'Z'

    message = formatted_date + method + path + query
    signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    authorization = (
        f"CEA algorithm=HmacSHA256, "
        f"access-key={access_key}, "
        f"signed-date={formatted_date}, "
        f"signature={signature}"
    )

    url = f"https://api-gateway.coupang.com{path}?{query}"
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("productData", [])
        else:
            st.error(f"쿠팡 API 호출 실패 (상태 코드: {response.status_code})\n{response.text}")
            return []
    except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")
        return []


# ==========================================
# 2. Gemini로 스레드 홍보글 생성
# ==========================================
def generate_threads_post(product, api_key, model_name="gemini-flash-latest", max_retries=3):
    """Gemini API를 사용하여 Threads 홍보글을 생성합니다. (429 자동 재시도 포함)"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    prompt = f"""
다음 쿠팡 상품 정보를 바탕으로 Threads에 올릴 홍보글 "본문"을 작성해줘.

[상품 정보]
- 상품명: {product['title']}
- 가격: {product['price']}원

[작성 가이드라인]
1. 톤앤매너: 친근하고 솔직한 반말 사용 (~함, ~거든, ~잖아 등 자연스러운 어조)
2. 훅킹(첫 문장): 스크롤을 멈추게 만드는 강력한 자극/공감형 문구로 시작
   (예: "진작 안 사고 뭐 했지?", "이거 모르면 손해임")
3. 정보성 강조: 스펙 나열이 아니라 이 제품이 왜 필요한지, 실사용 관점에서
   체감되는 장점 위주로 정보성 있게 작성
4. 해시태그: 연관성 높은 해시태그 3개 포함 (본문 마지막에)
5. 전체 300자 내외, 너무 광고 티 나지 않게 자연스러운 후기 톤 유지
6. 절대 링크나 URL을 본문에 넣지 마 (링크는 별도의 다음 페이지에 자동으로 첨부됨)
7. 공정거래위원회 문구나 협찬/수수료 관련 문구도 절대 넣지 마 (이것도 별도로 자동 첨부됨)

결과는 완성된 게시글 본문만 출력해줘. 다른 설명은 붙이지 마.
"""

    last_error = None
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            last_error = e
            err_str = str(e)
            # 429 / 리소스 소진(RESOURCE_EXHAUSTED) 오류인 경우에만 재시도
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
                wait_time = (2 ** attempt) * 3  # 3s, 6s, 12s
                st.warning(
                    f"⏳ 요청 한도(429)에 걸렸습니다. {wait_time}초 후 자동 재시도합니다... "
                    f"({attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
                continue
            else:
                # 429가 아닌 다른 오류는 즉시 반환
                return f"Gemini API 호출 중 오류가 발생했습니다: {e}"

    return (
        "⚠️ Gemini API 요청 한도(429)를 초과했습니다.\n"
        "- 무료 티어는 분당/일일 요청 수 제한이 있어요. 1분 정도 기다렸다가 다시 시도해보세요.\n"
        "- 계속 발생하면 https://aistudio.google.com/ 에서 사용량과 한도를 확인하거나,\n"
        "  결제(Billing)를 연결해 유료 티어로 전환하면 한도가 크게 늘어납니다.\n"
        f"- 원본 오류: {last_error}"
    )


# ==========================================
# 3. Threads 자동 업로드 (Meta Threads API)
# ==========================================
THREADS_API_BASE = "https://graph.threads.net/v1.0"

# 쿠팡 파트너스 공정거래위원회 고지 문구 (항상 링크 바로 위에 고정)
COUPANG_DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."


def _create_and_publish_thread(user_id, access_token, text, reply_to_id=None):
    """
    Threads 게시글(또는 답글) 하나를 생성+발행합니다.
    reply_to_id를 넘기면 해당 게시물에 대한 답글(다음 페이지)로 등록됩니다.
    """
    create_url = f"{THREADS_API_BASE}/{user_id}/threads"
    create_params = {
        "media_type": "TEXT",
        "text": text,
        "access_token": access_token,
    }
    if reply_to_id:
        create_params["reply_to_id"] = reply_to_id

    create_res = requests.post(create_url, data=create_params)
    create_data = create_res.json()

    if "id" not in create_data:
        return False, f"컨테이너 생성 실패: {create_data}"

    creation_id = create_data["id"]

    # Threads 서버가 컨테이너를 처리할 시간을 잠깐 대기 (권장 사항)
    time.sleep(2)

    publish_url = f"{THREADS_API_BASE}/{user_id}/threads_publish"
    publish_params = {
        "creation_id": creation_id,
        "access_token": access_token,
    }
    publish_res = requests.post(publish_url, data=publish_params)
    publish_data = publish_res.json()

    if "id" in publish_data:
        return True, publish_data["id"]
    else:
        return False, f"게시 실패: {publish_data}"


def post_to_threads(user_id, access_token, main_text, link):
    """
    Threads에 2개의 스레드로 나눠서 게시합니다.
    1번째(본문): main_text
    2번째(답글/다음 페이지): 공정위 고지 문구 + 제품 링크
    두 게시물 모두 성공해야 최종 성공으로 판단합니다.
    """
    try:
        # 1단계: 본문 게시
        ok, main_result = _create_and_publish_thread(user_id, access_token, main_text)
        if not ok:
            return False, f"[본문 게시 실패] {main_result}"

        main_post_id = main_result

        # Threads 서버 반영 대기
        time.sleep(1)

        # 2단계: 공정위 문구 + 링크를 답글(다음 페이지)로 게시
        reply_text = f"{COUPANG_DISCLOSURE}\n{link}"
        ok2, reply_result = _create_and_publish_thread(
            user_id, access_token, reply_text, reply_to_id=main_post_id
        )
        if not ok2:
            return False, (
                f"[본문은 게시됨 (ID: {main_post_id}) / "
                f"공정위 문구+링크 답글 게시 실패] {reply_result}"
            )

        return True, f"본문 ID: {main_post_id}, 답글(링크) ID: {reply_result}"

    except Exception as e:
        return False, f"Threads 업로드 중 오류 발생: {e}"


# ==========================================
# 4. 메인 화면 UI
# ==========================================
st.markdown("### 🔎 1. 쿠팡 상품 검색")

keyword = st.text_input("검색어", placeholder="예: 무선 이어폰")

if st.button("🔍 검색"):
    if not coupang_access or not coupang_secret:
        st.warning("⚠️ 사이드바에 쿠팡 API 키를 먼저 입력해주세요!")
    elif not keyword.strip():
        st.warning("⚠️ 검색어를 입력해주세요.")
    else:
        with st.spinner("쿠팡에서 상품을 검색 중입니다..."):
            products = search_coupang_products(keyword, coupang_access, coupang_secret)
            if products:
                st.session_state.products = products
                st.session_state.generated_posts = {}
                st.success(f"총 {len(products)}개의 상품을 찾았습니다!")
            else:
                st.session_state.products = []
                st.info("검색 결과가 없거나 API 호출에 실패했습니다.")

# 검색 결과 출력
if st.session_state.products:
    st.markdown("### 📝 2. 상품 선택 → 홍보글 생성 → Threads 업로드")

    for idx, prod in enumerate(st.session_state.products):
        cols = st.columns([1, 3])

        with cols[0]:
            if "productImage" in prod:
                st.image(prod["productImage"], use_container_width=True)

        with cols[1]:
            st.write(f"**{prod.get('productName')}**")
            st.write(f"💰 가격: {format(prod.get('productPrice', 0), ',')}원")
            st.markdown(f"[🔗 상품 링크 확인하기]({prod.get('productUrl')})")

            product_info = {
                "title": prod.get("productName", ""),
                "price": prod.get("productPrice", 0),
                "link": prod.get("productUrl", ""),
            }

            # 홍보글 생성 버튼
            if st.button("✨ 스레드 홍보글 생성", key=f"gen_{idx}"):
                if not gemini_key:
                    st.error("⚠️ 사이드바에 Gemini API 키를 먼저 입력해주세요!")
                else:
                    with st.spinner(f"Gemini({gemini_model})가 홍보글을 작성하고 있습니다..."):
                        post_content = generate_threads_post(product_info, gemini_key, gemini_model)
                        st.session_state.generated_posts[idx] = post_content

            # 생성된 글이 있으면 표시 + 수정 + 업로드
            if idx in st.session_state.generated_posts:
                st.markdown("#### 📱 생성된 스레드 게시글 (수정 가능)")
                edited_text = st.text_area(
                    "1페이지 (본문) — 업로드 전 자유롭게 수정하세요",
                    value=st.session_state.generated_posts[idx],
                    height=220,
                    key=f"text_{idx}"
                )
                st.session_state.generated_posts[idx] = edited_text

                st.markdown("**2페이지 (답글) — 공정위 문구 + 링크 (자동 고정, 링크 위 문구 필수)**")
                st.text_area(
                    "자동으로 첨부되는 내용입니다 (수정 불가)",
                    value=f"{COUPANG_DISCLOSURE}\n{product_info['link']}",
                    height=100,
                    key=f"disclosure_preview_{idx}",
                    disabled=True,
                )

                selected_accounts = [
                    acc for acc in threads_accounts
                    if acc["use"] and acc["user_id"] and acc["token"]
                ]

                if st.button(
                    f"🚀 선택된 {len(selected_accounts)}개 계정에 동시 업로드 (본문+링크 답글)",
                    key=f"upload_{idx}",
                    disabled=len(selected_accounts) == 0,
                ):
                    if not edited_text.strip():
                        st.warning("⚠️ 업로드할 내용이 비어 있습니다.")
                    else:
                        for acc in selected_accounts:
                            with st.spinner(f"{acc['label']}에 게시 중입니다 (본문 → 답글)..."):
                                ok, result = post_to_threads(
                                    acc["user_id"],
                                    acc["token"],
                                    edited_text,
                                    product_info["link"],
                                )
                                if ok:
                                    st.success(
                                        f"✅ {acc['label']} 업로드 완료! ({result})"
                                    )
                                else:
                                    st.error(f"❌ {acc['label']} 업로드 실패: {result}")

                if len(selected_accounts) == 0:
                    st.caption("⚠️ 사이드바에서 업로드할 Threads 계정을 최소 1개 이상 체크하고, User ID/Token을 입력해주세요.")

        st.markdown("---")