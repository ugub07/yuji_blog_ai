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
# [팝업 없는 초고속 무소음 스마트 복사기]
# ==========================================
def render_smart_copy_button(text_to_copy, button_label="📋 복사", is_table=False, table_data=None):
    btn_id = f"btn_{abs(hash(button_label + str(text_to_copy)))}"
    
    if is_table and table_data:
        html_rows = "".join([f"<tr><th style='background:#f4f6f8;border:1px solid #ddd;padding:8px;text-align:left;font-weight:bold;'>{r.get('항목','')}</th><td style='border:1px solid #ddd;padding:8px;'>{r.get('내용','')}</td></tr>" for r in table_data])
        html_table = f"<table style='border-collapse:collapse;width:100%;border:1px solid #ddd;'><tbody>{html_rows}</tbody></table>"
        
        tsv_lines = [f"{row.get('항목', '')}\t{row.get('내용', '')}" for row in table_data]
        plain_text = "\\n".join(tsv_lines).replace('"', '\\"').replace("'", "\\'").replace("\r", "")
        clean_html = html_table.replace('"', '\\"').replace("'", "\\'").replace("\r", "").replace("\n", "")

        js_code = f"""
        <button id="{btn_id}" onclick="
            const btn = document.getElementById('{btn_id}');
            const plain = '{plain_text}';
            const html = '{clean_html}';
            if (navigator.clipboard && window.ClipboardItem) {{
                const blobHtml = new Blob([html], {{ type: 'text/html' }});
                const blobPlain = new Blob([plain], {{ type: 'text/plain' }});
                const item = new ClipboardItem({{ 'text/html': blobHtml, 'text/plain': blobPlain }});
                navigator.clipboard.write([item]).then(() => flashBtn(btn)).catch(() => {{
                    navigator.clipboard.writeText(plain).then(() => flashBtn(btn));
                }});
            }} else {{
                const ta = document.createElement('textarea');
                ta.value = plain;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                flashBtn(btn);
            }}
            function flashSpan(b) {{
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
st.caption("무소속 원클릭 복사 | 몽글몽글 친근한 육아 구어체 | 고속 유료 라인")

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

category = st.selectbox("카테고리", ["책육아", "장난감·교구", "방문한 곳", "물건", "음식"])
item_name = st.text_input("장소 / 상호 / 제품명", placeholder="예: DK My First Collection 7권 세트")
target_keyword = st.text_input("필수 키워드 (쉼표 구분)", placeholder="예: DK보드북, 유아영어그림책, 세이펜에디션")
extra_notes = st.text_area("상세 메모 및 솔직 후기 팁", placeholder="실제 아이 반응, 활용팁, 솔직한 느낌을 적어주세요.", height=70)

st.write("---")

# 1. 정보 사진 등록
st.markdown("#### 📌 메뉴판 / 스펙 / 정보 사진 (선택)")
info_files = st.file_uploader(
    "정보 사진 선택 (개별 사진 또는 ZIP 파일)", 
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
st.info("💡 **사진들을 ZIP으로 압축**해서 올리거나 다중 선택해서 올려주세요.")

body_files = st.file_uploader(
    "본문 사진 선택 (사진 개별 다중 선택 또는 ZIP 압축 파일)", 
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

    with st.spinner(f"⚡ 사진 {len(st.session_state['cur_body_images'])}장 분석 및 다정한 글 작성 중..."):
        pasted_images = st.session_state["cur_body_images"]
        info_images = st.session_state["cur_info_images"]

        system_instruction = """
너는 네이버 블로그 스마트블록 상위 노출을 전문으로 하는 인기 육아 인플루언서야.
기계적인 설명조나 딱딱한 문체를 절대 쓰지 말고, 친한 육아 동지 엄마에게 톡으로 수다 떨듯 다정하고 감칠맛 나는 구어체로 작성해.

[문체 및 작성 핵심 규칙]
1. [다정한 구어체 종결어미]: '~더라고요', '~했잖아요', '~하기 딱 좋더라고요', '~손이 자주 가요', '~하는 재미가 쏠쏠해요', '~느껴지더라고요' 위주로 풍성하게 서술.
2. [절대 금지 어휘]: '제격이에요', '쏙쏙 골라', '시선을 사로잡는', '고사리손', '이해가 쏙쏙 돼요', '취향 저격', '꿀템', '대박', '침 꼴깍 삼키며', '큰 축복 같아요', '다채로운', '소장 가치', '~했답니다', '~느낌이었답니다'
3. [생생한 육아 디테일]: 아이가 책장을 넘길 때의 손길, 장난감을 만지며 웃던 표정, 실제 제형이나 마감 등 사진 속 포인트들을 짚어내어 3~4줄 내외로 몰입감 있게 작성.
4. [태그 # 기호 금지]: 해시태그 '#' 없이 단어만 작성.

반드시 아래 JSON 포맷으로만 응답:
{
  "titles": ["1안 제목", "2안 제목", "3안 제목"],
  "main_image_tags": "대표사진 하단 태그 단어 3~4개 (# 없이 띄어쓰기 구분)",
  "intro_description": "메인 썸네일 아래 들어갈 2~3줄의 구매 계기와 첫인상 (엄마의 솔직한 독백 스타일)",
  "qna_questions": [
    "질문 1 (예: 아이가 지루해하지 않고 잘 가지고 놀까요?)",
    "질문 2",
    "질문 3"
  ],
  "intro_bridge": "저도 들이기 전에 가장 궁금했던 부분들인데, 직접 써보면서 느낀 점들을 솔직하게 풀어볼게요!",
  "summary_table_data": [
    {"항목": "상호/제품명", "내용": "구체적인 제품명"},
    {"항목": "종류/구성", "내용": "상세 구성 내용"},
    {"항목": "권장연령/규격", "내용": "대상 연령 및 사이즈"},
    {"항목": "가격/스펙", "내용": "가격 및 주요 스펙"},
    {"항목": "구매처/팁", "내용": "구매처 및 꿀팁"}
  ],
  "body_sections": [
    {
      "photo_index": 2,
      "subheading": "스마트에디터용 소제목",
      "tags": "사진 하단 태그 2~3단어 (# 제외)",
      "content": "사진 장면에 맞춘 몰입감 있는 본문 (아이 반응이나 실제 사용 느낌을 다정한 어조로 3~4줄 작성)"
    }
  ],
  "review_table_data": [
    {"항목": "장점", "내용": "실제 써보고 느낀 솔직한 장점 2~3줄"},
    {"항목": "아쉬운 점", "내용": "솔직한 아쉬운 점 2~3줄"},
    {"항목": "활용 꿀팁", "내용": "알아두면 유용한 꿀팁 2~3줄"},
    {"항목": "추천 대상", "내용": "어떤 아기나 엄마에게 딱인지 2~3줄"}
  ],
  "closing_remarks": "마무리 총평 및 소감 2줄",
  "naver_tags": ["태그1", "태그2", "태그3", "태그4", "태그5", "태그6", "태그7", "태그8", "태그9", "태그10"]
}
"""

        prompt_text = f"""
[카테고리]: {category}
[장소/상호/교구/제품명]: {item_name}
[필수 핵심 키워드]: {target_keyword}
[상세 메모 및 솔직 후기 팁]: {extra_notes if extra_notes else "사진 디테일을 보고 실제 아이와 함께 겪은 경험담 위주로 다정하고 생생하게 작성해줘"}

등록된 정보 사진 {len(info_images)}장과 본문 사진 {len(pasted_images)}장을 분석해서 포스팅을 작성해줘.
본문 사진 2번부터 {len(pasted_images)}번까지 각 사진에 해당하는 body_sections를 빠짐없이 순서대로 작성해줘.
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
    
    st.success("🎉 작성이 완료되었습니다! 복사 버튼을 눌러 에디터에 붙여넣으세요.")
    
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

    with st.expander("📊 상단 요약 표 (스마트에디터 표 자동 변환)", expanded=True):
        s_table = data.get("summary_table_data", [])
        if s_table:
            for r in s_table:
                st.markdown(f"• **{r.get('항목')}**: {r.get('내용')}")
            render_smart_copy_button("", button_label="📊 요약 표 복사 (스마트에디터에 붙여넣기 시 정식 표 변환)", is_table=True, table_data=s_table)

    with st.expander("✍️ 사진별 소제목 & 본문 내용 (다정한 육아 구어체)", expanded=True):
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
            render_smart_copy_button("", button_label="📊 총평 표 복사 (스마트에디터에 붙여넣기 시 정식 표 변환)", is_table=True, table_data=r_table)
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
