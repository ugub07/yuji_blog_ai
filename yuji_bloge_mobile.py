import io
import json
import os
import re
import zipfile
import time
import warnings
import streamlit as st
from PIL import Image
import google.genai as genai

warnings.filterwarnings("ignore")
os.environ["GRPC_VERBOSITY"] = "ERROR"

# ==========================================
# [페이지 설정 및 비밀번호 보안]
# ==========================================
st.set_page_config(
    page_title="블로그 맞춤 초안 생성기",
    page_icon="✍️",
    layout="wide"
)

MY_PASSWORD = "1234"
BACKUP_FILE_PATH = "last_blog_result.json"

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    st.title("🔒 블로그 생성기 로그인")
    pwd = st.text_input("접속 비밀번호를 입력하세요", type="password")
    if st.button("접속하기", use_container_width=True):
        if pwd == MY_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 일치하지 않습니다.")
    st.stop()

# ==========================================
# [설정 및 신규 유료 API 키]
# ==========================================
GEMINI_API_KEY = "AQ.Ab8RN6L0BRlxvtCiCV8AAE6HPgjsuqLn6UWIcQXN0_7So3THuA"
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# [세션 상태 관리]
# ==========================================
if "cur_body_images" not in st.session_state:
    st.session_state["cur_body_images"] = []
if "cur_info_images" not in st.session_state:
    st.session_state["cur_info_images"] = []
if "processed_upload_token" not in st.session_state:
    st.session_state["processed_upload_token"] = ""
if "result_data" not in st.session_state:
    st.session_state["result_data"] = None


# ==========================================
# [초경량 이미지 리사이징 & 자연 정렬 헬퍼]
# ==========================================
def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def process_to_light_image(file_bytes, max_dim=380):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70, optimize=True)
        buf.seek(0)
        return Image.open(buf)
    except Exception:
        return None


# ==========================================
# [중간 절단 방어 JSON 복구 엔진]
# ==========================================
def salvage_broken_json(raw_str):
    s = raw_str.strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = s[3:]
    if s.endswith("```"):
        s = s[:-3]
    s = s.strip()

    try:
        return json.loads(s)
    except Exception:
        pass

    fixed = s
    quote_count = fixed.count('"') - fixed.count('\\"')
    if quote_count % 2 != 0:
        fixed += '"'
    fixed += ']' * max(0, fixed.count('[') - fixed.count(']'))
    fixed += '}' * max(0, fixed.count('{') - fixed.count('}'))
    try:
        return json.loads(fixed)
    except Exception:
        pass

    salvaged = {
        "titles": [],
        "main_image_tags": "",
        "intro_description": "",
        "qna_questions": [],
        "intro_bridge": "",
        "summary_table_data": [],
        "body_sections": [],
        "review_table_data": [],
        "closing_remarks": "",
        "naver_tags": []
    }

    t_match = re.search(r'"titles"\s*:\s*\[(.*?)\]', s, re.DOTALL)
    if t_match:
        salvaged["titles"] = re.findall(r'"(.*?)"', t_match.group(1))

    m_tag = re.search(r'"main_image_tags"\s*:\s*"(.*?)"', s)
    if m_tag: salvaged["main_image_tags"] = m_tag.group(1)

    m_intro = re.search(r'"intro_description"\s*:\s*"(.*?)"', s)
    if m_intro: salvaged["intro_description"] = m_intro.group(1)

    m_bridge = re.search(r'"intro_bridge"\s*:\s*"(.*?)"', s)
    if m_bridge: salvaged["intro_bridge"] = m_bridge.group(1)

    s_match = re.search(r'"summary_table_data"\s*:\s*\[(.*?)\]', s, re.DOTALL)
    if s_match:
        rows = re.findall(r'\{\s*"항목"\s*:\s*"(.*?)"\s*,\s*"내용"\s*:\s*"(.*?)"\s*\}', s_match.group(1))
        salvaged["summary_table_data"] = [{"항목": r[0], "내용": r[1]} for r in rows]

    body_matches = re.findall(r'\{\s*"photo_index"\s*:\s*(\d+).*?"subheading"\s*:\s*"(.*?)".*?"tags"\s*:\s*"(.*?)".*?"content"\s*:\s*"(.*?)"\s*\}', s, re.DOTALL)
    for bm in body_matches:
        salvaged["body_sections"].append({
            "photo_index": int(bm[0]),
            "subheading": bm[1],
            "tags": bm[2],
            "content": bm[3]
        })

    r_match = re.search(r'"review_table_data"\s*:\s*\[(.*?)\]', s, re.DOTALL)
    if r_match:
        rows = re.findall(r'\{\s*"항목"\s*:\s*"(.*?)"\s*,\s*"내용"\s*:\s*"(.*?)"\s*\}', r_match.group(1))
        salvaged["review_table_data"] = [{"항목": r[0], "내용": r[1]} for r in rows]

    m_close = re.search(r'"closing_remarks"\s*:\s*"(.*?)"', s)
    if m_close: salvaged["closing_remarks"] = m_close.group(1)

    tag_match = re.search(r'"naver_tags"\s*:\s*\[(.*?)\]', s, re.DOTALL)
    if tag_match:
        salvaged["naver_tags"] = re.findall(r'"(.*?)"', tag_match.group(1))

    return salvaged


# ==========================================
# [무소음 스마트 복사기 (텍스트 표 지원 포함)]
# ==========================================
def render_smart_copy_button(text_to_copy, button_label="📋 복사", is_table=False, table_data=None):
    btn_id = f"btn_{abs(hash(button_label + str(text_to_copy)))}"
    
    if is_table and table_data:
        tsv_lines = [f"{row.get('항목', '')} : {row.get('내용', '')}" for row in table_data]
        plain_text = "\\n".join(tsv_lines).replace('"', '\\"').replace("'", "\\'").replace("\r", "")

        js_code = f"""
        <button id="{btn_id}" onclick="
            const btn = document.getElementById('{btn_id}');
            const text = '{plain_text}';
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(text).then(() => flashBtn(btn));
            }} else {{
                const ta = document.createElement('textarea');
                ta.value = text;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                flashBtn(btn);
            }}
            function flashBtn(b) {{
                const orig = b.innerText;
                b.innerText = '✨ 복사완료!';
                b.style.backgroundColor = '#028a43';
                setTimeout(() => {{
                    b.innerText = orig;
                    b.style.backgroundColor = '#03c75a';
                }}, 600);
            }}
        " style="background-color:#03c75a; color:white; border:none; padding:10px 14px; border-radius:6px; cursor:pointer; font-weight:bold; font-size:14px; width:100%; touch-action: manipulation;">
        {button_label}
        </button>
        """
    else:
        content = text_to_copy.replace("#", "").replace("'", "\\'").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
        js_code = f"""
        <button id="{btn_id}" onclick="
            const btn = document.getElementById('{btn_id}');
            const text = '{content}';
            if (navigator.clipboard && navigator.clipboard.writeText) {{
                navigator.clipboard.writeText(text).then(() => flashBtn(btn));
            }} else {{
                const ta = document.createElement('textarea');
                ta.value = text;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                flashBtn(btn);
            }}
            function flashBtn(b) {{
                const orig = b.innerText;
                b.innerText = '✨ 복사완료!';
                b.style.backgroundColor = '#339af0';
                setTimeout(() => {{
                    b.innerText = orig;
                    b.style.backgroundColor = '#f1f3f5';
                    b.style.color = '#333';
                }}, 600);
            }}
        " style="background-color:#f1f3f5; color:#333; border:1px solid #ced4da; padding:9px 12px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600; width:100%; touch-action: manipulation;">
        {button_label}
        </button>
        """
    st.components.v1.html(js_code, height=48)


# ==========================================
# [메인 화면 구성]
# ==========================================
st.title("✍️ 네이버 블로그 맞춤 초안 생성기")
st.caption("카테고리별 맞춤 뉘앙스 | 하용이(24년 10월생) 연동 | 찐후기 구어체")

# 상시 복구 바 영역
rec_col1, rec_col2 = st.columns([3, 1])
has_backup = os.path.exists(BACKUP_FILE_PATH)
rec_col1.info("💡 새로고침이나 화면 전환으로 글이 사라졌다면 오른쪽 복구 버튼을 누르세요.")
if rec_col2.button("🔄 최근 생성 글 복구", use_container_width=True, disabled=not has_backup):
    if has_backup:
        try:
            with open(BACKUP_FILE_PATH, "r", encoding="utf-8") as f:
                st.session_state["result_data"] = json.load(f)
            st.success("이전 생성 글을 성공적으로 복구했습니다!")
            st.rerun()
        except Exception as e:
            st.error(f"복구 실패: {e}")
    else:
        st.warning("저장된 이전 백업이 없습니다.")

category = st.selectbox(
    "블로그 카테고리 선택", 
    [
        "책육아", 
        "하용이 장난감·교구", 
        "내가 방문한 곳", 
        "내가 써본 물건", 
        "내가 먹은 음식", 
        "누리와 리치", 
        "하용이 육아", 
        "여행", 
        "일상"
    ]
)

item_name = st.text_input("장소 / 상호 / 제품 / 주제명", placeholder="예: 체스턴스 경주 더 테라스 호텔")
target_keyword = st.text_input("필수 검색 키워드 (쉼표 구분)", placeholder="예: 경주키즈호텔, 경주애견동반호텔, 경주숙소추천")
extra_notes = st.text_area("상세 메모 및 솔직 후기 팁", placeholder="실제 느낀 점, 아기 반응, 주차/예약 꿀팁 등을 적어주세요.", height=70)

st.write("---")

# 1. 정보 사진 등록
st.markdown("#### 📌 메뉴판 / 스펙 / 정보 사진 (선택)")
info_files = st.file_uploader(
    "정보 사진 선택 (파일 선택, ZIP 또는 캡쳐 이미지 드래그/붙여넣기)", 
    type=["jpg", "jpeg", "png", "webp", "zip"],
    accept_multiple_files=True,
    key="uploader_info"
)

if info_files:
    parsed_info = []
    for f in info_files:
        f.seek(0)
        b = f.read()
        if f.name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(b)) as z:
                    for filename in sorted(z.namelist(), key=natural_sort_key):
                        if "__MACOSX" in filename or filename.startswith("."):
                            continue
                        if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            im = process_to_light_image(z.read(filename))
                            if im:
                                parsed_info.append(im)
            except Exception:
                pass
        else:
            im = process_to_light_image(b)
            if im:
                parsed_info.append(im)
    st.session_state["cur_info_images"] = parsed_info

if st.session_state["cur_info_images"]:
    st.caption(f"등록된 정보 사진: 총 {len(st.session_state['cur_info_images'])}장")

st.write("---")

# 2. 본문 사진 등록
st.markdown("#### 📷 본문 사진 (필수)")
st.info("💡 **팁:** 스크린샷 캡쳐 후 업로더 창에 바로 **`Ctrl + V`**를 누르거나, 여러 장을 한 번에 드래그해서 넣으세요!")

body_files = st.file_uploader(
    "본문 사진 선택 (파일 다중선택, ZIP 또는 클립보드 캡쳐 붙여넣기)", 
    type=["jpg", "jpeg", "png", "webp", "zip"],
    accept_multiple_files=True,
    key="uploader_body"
)

if body_files:
    current_token = "|".join([f"{f.name}_{f.size}" for f in body_files])
    if st.session_state["processed_upload_token"] != current_token:
        loaded_imgs = []
        with st.spinner("📦 사진을 추출하고 가볍게 최적화하는 중입니다..."):
            for f in body_files:
                f.seek(0)
                b = f.read()
                if f.name.lower().endswith(".zip"):
                    try:
                        with zipfile.ZipFile(io.BytesIO(b)) as z:
                            file_list = sorted(z.namelist(), key=natural_sort_key)
                            for filename in file_list:
                                if "__MACOSX" in filename or os.path.basename(filename).startswith("."):
                                    continue
                                if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                                    img_bytes = z.read(filename)
                                    im = process_to_light_image(img_bytes)
                                    if im:
                                        loaded_imgs.append(im)
                    except Exception as e:
                        st.error(f"ZIP 해제 중 오류: {e}")
                else:
                    im = process_to_light_image(b)
                    if im:
                        loaded_imgs.append(im)

        st.session_state["cur_body_images"] = loaded_imgs
        st.session_state["processed_upload_token"] = current_token
        st.rerun()

# 본문 사진 순서 조정 및 미리보기 영역
if st.session_state["cur_body_images"]:
    img_list = st.session_state["cur_body_images"]
    
    col_t1, col_t2 = st.columns([3, 1])
    col_t1.success(f"🎉 총 {len(img_list)}장의 사진이 등록되었습니다. 순서를 배치해 주세요!")
    if col_t2.button("🗑️ 사진 전체 삭제"):
        st.session_state["cur_body_images"] = []
        st.session_state["processed_upload_token"] = ""
        st.rerun()

    for i in range(0, len(img_list), 2):
        cols = st.columns(2)
        for j in range(2):
            idx = i + j
            if idx < len(img_list):
                with cols[j]:
                    st.image(img_list[idx], caption=f"사진 {idx+1}{' (⭐ 대표 썸네일)' if idx == 0 else ''}", use_container_width=True)
                    
                    b_cols = st.columns(5)
                    if idx > 0:
                        if b_cols[0].button("⏮️맨앞", key=f"first_{idx}", help="1번 대표 사진으로 이동"):
                            item = img_list.pop(idx)
                            img_list.insert(0, item)
                            st.rerun()
                    if idx > 0:
                        if b_cols[1].button("⬅️앞", key=f"up_{idx}"):
                            img_list[idx - 1], img_list[idx] = img_list[idx], img_list[idx - 1]
                            st.rerun()
                    if idx < len(img_list) - 1:
                        if b_cols[2].button("➡️뒤", key=f"down_{idx}"):
                            img_list[idx + 1], img_list[idx] = img_list[idx], img_list[idx + 1]
                            st.rerun()
                    if idx < len(img_list) - 1:
                        if b_cols[3].button("⏭️맨뒤", key=f"last_{idx}", help="마지막 사진으로 이동"):
                            item = img_list.pop(idx)
                            img_list.append(item)
                            st.rerun()
                    if b_cols[4].button("❌", key=f"del_{idx}", help="이 사진 삭제"):
                        img_list.pop(idx)
                        st.rerun()
        st.write("")

st.write("---")

# ==========================================
# [생성 버튼 및 호출 로직]
# ==========================================
if st.button("✨ 블로그 맞춤 초안 생성하기", type="primary", use_container_width=True):
    if not item_name or not target_keyword:
        st.warning("명칭과 필수 키워드를 입력해 주세요.")
        st.stop()
        
    if not st.session_state["cur_body_images"]:
        st.warning("본문 사진을 최소 1장 이상 등록해 주세요.")
        st.stop()

    with st.spinner(f"⚡ 사진 {len(st.session_state['cur_body_images'])}장 분석 및 찐후기 스타일 작성 중..."):
        pasted_images = st.session_state["cur_body_images"]
        info_images = st.session_state["cur_info_images"]

        system_instruction = f"""
너는 네이버 블로그 스마트블록 상위 노출과 VIEW 탭 장악을 전문으로 하는 인기 파워 인플루언서야.
작성자 본인(유지)은 2024년 10월 22일생 딸 **하용이**를 키우고 있는 엄마야. 
현재 선택된 카테고리: [{category}]에 맞춰 전체적인 뉘앙스와 브릿지 문구를 완전히 다르게 설정해서 작성해.

[카테고리별 맞춤 뉘앙스 지침]
1. [책육아 / 하용이 장난감·교구]: 아기가 직접 가지고 놀거나 책을 읽을 때의 반응, 개월 수(하용이 나이 기준 연계), 전집/교구 비교(구버전/신버전, AS 정보, 당근 꿀팁 등)를 생생하게 녹여낼 것.
2. [내가 방문한 곳 / 여행 / 누리와 리치]: 장소의 분위기, 주차 및 웨이팅 팁, 아기(또는 반려동물)와 함께 방문했을 때의 리얼한 소감과 동선 꿀팁 위주로 작성할 것 ("들이기 전에~" 같은 엉뚱한 책 관련 브릿지 절대 금지!).
3. [내가 써본 물건 / 내가 먹은 음식 / 하용이 육아 / 일상]: 내돈내산 실사용 후기 뉘앙스로, 뼈 때리는 장단점과 꿀조합 레시피/활용법을 친근한 독백으로 풀어낼 것.

[작성 핵심 규칙 - 절대 엄수]
1. [리드미컬한 줄바꿈]: AI처럼 길게 쓰지 말고, 모바일에서 보기 좋게 한 줄씩 짧게 툭툭 끊어 치기.
2. [감정 이모티콘 자연스럽게 배치]: 상황에 맞춰 `ㅎㅎ`, `ㅋㅋㅋ`, `ㅠㅠ`를 문장 끝에 자연스럽게 섞을 것.
3. [검색 유입형 소제목]: 모든 사진마다 소제목을 억지로 붙이지 말고, 글의 흐름상 전환점이 되는 중요 구간에만 검색 유입 키워드가 녹아든 찰진 소제목을 배치할 것 (성의 없는 '안내판 사진' 같은 제목 절대 금지).
4. [검색 유입형 사진 태그]: 사진마다 중복 태그를 반복하지 말고, 해당 사진의 핵심 디테일을 담은 서로 다른 롱테일 태그 2~3개 작성.
5. [절대 금지 어휘]: '제격이에요', '쏙쏙 골라', '시선을 사로잡는', '고사리손', '이해가 쏙쏙 돼요', '취향 저격', '꿀템', '대박', '침 꼴깍 삼키며', '큰 축복 같아요', '다채로운', '소장 가치', '~했답니다', '~느낌이었답니다'
6. [태그 # 기호 금지]: 해시태그 '#' 없이 순수 단어만 작성.

반드시 아래 JSON 포맷으로만 응답:
{{
  "titles": ["1안 제목", "2안 제목", "3안 제목"],
  "main_image_tags": "대표사진 하단 검색 유입 태그 3~4개 (# 없이 띄어쓰기 구분)",
  "intro_description": "메인 썸네일 아래 들어갈 2~3줄의 방문/구매/시작 계기와 기대감을 높여주는 리얼 첫인상 독백",
  "qna_questions": [
    "질문 1 (독자들이 가장 궁금해할 핵심 포인트)",
    "질문 2",
    "질문 3"
  ],
  "intro_bridge": "선택된 카테고리에 맞는 자연스러운 브릿지 문구 (예: 방문/여행 후기라면 '직접 다녀오면서 느낀 꿀팁들과 아쉬운 점들을 솔직하게 풀어볼게요!', 책육아라면 '직접 읽혀보며 느낀 점들을 솔직하게 공유해 드릴게요!')",
  "summary_table_data": [
    {{"항목": "상호/제품명", "내용": "구체적인 이름"}},
    {{"항목": "구성/위치", "내용": "상세 정보 또는 위치"}},
    {{"항목": "가격/시간", "내용": "가격 또는 영업시간"}},
    {{"항목": "특징/스펙", "내용": "주요 특징 및 스펙"}},
    {{"항목": "핵심 꿀팁", "내용": "알아두면 유용한 팁"}}
  ],
  "body_sections": [
    {{
      "photo_index": 2,
      "subheading": "검색 유입과 호기심을 잡는 매력적인 소제목 (필요한 사진에만 작성, 없으면 빈 문자열 가능)",
      "tags": "사진 하단 검색 태그 2~3단어 (# 제외)",
      "content": "사진 속 디테일을 콕 집어내어 내가 직접 겪은 생생한 에피소드와 느낌을 담아 3~4줄로 몰입감 있게 작성"
    }}
  ],
  "review_table_data": [
    {{"항목": "장점", "내용": "실제 써보고 느낀 솔직한 장점 2~3줄"}},
    {{"항목": "아쉬운 점", "내용": "솔직한 아쉬운 점이나 주의할 점 2~3줄"}},
    {{"항목": "이용/활용 꿀팁", "내용": "미리 알면 좋은 실용적인 팁 2~3줄"}},
    {{"항목": "추천 대상", "내용": "어떤 분들에게 꼭 맞는지 2~3줄"}}
  ],
  "closing_remarks": "마무리 총평 및 소감 2줄",
  "naver_tags": ["태그1", "태그2", "태그3", "태그4", "태그5", "태그6", "태그7", "태그8", "태그9", "태그10"]
}}
"""

        prompt_text = f"""
[카테고리]: {category}
[장소/상호/교구/제품명]: {item_name}
[필수 핵심 키워드]: {target_keyword}
[상세 메모 및 솔직 후기 팁]: {extra_notes if extra_notes else "사진 디테일을 꼼꼼히 살펴보고 내가 직접 체험한 리얼한 후기처럼 생생하고 매력적으로 작성해줘"}

등록된 정보 사진 {len(info_images)}장과 본문 사진 {len(pasted_images)}장을 분석해서 포스팅을 작성해줘.
본문 사진 2번부터 {len(pasted_images)}번까지 각 사진에 해당하는 body_sections를 빠짐없이 순서대로 작성해줘.
카테고리 [{category}]에 알맞은 뉘앙스와 브릿지 문구를 적용하고, 소제목과 사진 태그는 검색 유입이 잘 되도록 키워드 중심의 완성도 높은 문구로 작성해줘.
순수 JSON 형식으로 응답해줘.
"""

        payload = [prompt_text]
        if info_images:
            payload.append("\n[메뉴판/정보 사진]:\n")
            payload.extend(info_images)
        payload.append("\n[본문 포스팅 사진]:\n")
        payload.extend(pasted_images)

        res = None
        try:
            res = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=payload,
                config=dict(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    max_output_tokens=8192
                )
            )
        except Exception as e:
            st.error(f"호출 오류가 발생했습니다: {e}")

        if res and res.text:
            try:
                data = salvage_broken_json(res.text)
                st.session_state["result_data"] = data
                
                with open(BACKUP_FILE_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                st.error(f"결과 데이터 복구 실패: {e}")

# ==========================================
# [결과 출력 리스트 렌더링]
# ==========================================
if st.session_state["result_data"]:
    data = st.session_state["result_data"]
    
    st.success("🎉 작성이 완료되었습니다! 아래 항목별 복사 버튼을 눌러 에디터에 붙여넣으세요.")
    
    with st.expander("📌 네이버 추천 제목 (선택 복사)", expanded=True):
        for i, t in enumerate(data.get("titles", []), start=1):
            st.markdown(f"**{i}안:** {t}")
            render_smart_copy_button(t, f"{i}안 제목 복사")
            st.write("")

    with st.expander("📷 사진 1 (대표 썸네일) 아래 구성 요소", expanded=True):
        tag_main = data.get("main_image_tags", "")
        if tag_main:
            st.markdown(f"**사진 1 태그:** `{tag_main}`")
            render_smart_copy_button(tag_main, "태그 복사")
            st.write("")
        
        intro_desc = data.get("intro_description", "")
        st.markdown(f"**[도입 설명]**\n\n{intro_desc}")
        render_smart_copy_button(intro_desc, "도입 설명글 복사")
        st.write("")
        
        qna_list = data.get("qna_questions", [])
        qna_txt = "\n".join(qna_list) if isinstance(qna_list, list) else str(qna_list)
        st.info(f"**[Q&A 질문 - 인용구용]**\n\n{qna_txt}")
        render_smart_copy_button(qna_txt, "Q&A 인용구 복사")
        st.write("")
        
        intro_bridge = data.get("intro_bridge", "")
        st.markdown(f"**[연결 본문글]**\n\n{intro_bridge}")
        render_smart_copy_button(intro_bridge, "연결 본문글 복사")

    with st.expander("📊 상단 요약 표 (텍스트 표 복사 - 에디터 직접 작성용)", expanded=True):
        s_table = data.get("summary_table_data", [])
        if s_table:
            for r in s_table:
                st.markdown(f"• **{r.get('항목')}**: {r.get('내용')}")
            render_smart_copy_button("", button_label="📊 요약 표 항목/내용 텍스트 복사 (에디터 표에 참고용)", is_table=True, table_data=s_table)

    with st.expander("✍️ 사진별 소제목 & 본문 내용 (검색 최적화 찐후기)", expanded=True):
        for sec in data.get("body_sections", []):
            p_idx = sec.get("photo_index", 2)
            st.markdown(f"#### 📷 본문 사진 {p_idx}")
            
            sub = sec.get("subheading", "")
            if sub:
                st.markdown(f"**소제목:** {sub}")
                render_smart_copy_button(sub, "소제목 복사")
                
            p_tag = sec.get("tags", "")
            if p_tag:
                st.markdown(f"**사진 태그:** `{p_tag}`")
                render_smart_copy_button(p_tag, "태그 복사")
                
            content = sec.get("content", "")
            if content:
                st.markdown(f"**본문 내용:**\n\n{content}")
                render_smart_copy_button(content, "본문 복사")
                
            st.divider()

    with st.expander("📊 하단 총평 표 & 솔직 후기 마무리", expanded=True):
        r_table = data.get("review_table_data", [])
        if r_table:
            for r in r_table:
                st.markdown(f"• **{r.get('항목')}**: {r.get('내용')}")
            render_smart_copy_button("", button_label="📊 총평 표 항목/내용 텍스트 복사 (에디터 표에 참고용)", is_table=True, table_data=r_table)
            st.write("")
            
        closing = data.get("closing_remarks", "")
        if closing:
            st.markdown(f"**[마무리 소감]**\n\n{closing}")
            render_smart_copy_button(closing, "총평 소감 복사")

    with st.expander("🏷️ 네이버 스마트에디터 등록용 태그", expanded=True):
        tags = data.get("naver_tags", [])
        if tags:
            comma_tags = ", ".join([t.replace("#", "").strip() for t in tags])
            st.code(comma_tags)
            render_smart_copy_button(comma_tags, "태그 전체 복사 (쉼표 구분)")
