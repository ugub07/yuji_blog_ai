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
# [설정 및 신규 무료 API 키]
# ==========================================
GEMINI_API_KEY = "AQ.Ab8RN6Kl5WbrGnOp2GKXYJmZroPMzk157fLR3Z8FrbcOlehR3Q"
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# [세션 상태 관리: 사진 보관함]
# ==========================================
if "cur_body_images" not in st.session_state:
    st.session_state["cur_body_images"] = []
if "last_body_sig" not in st.session_state:
    st.session_state["last_body_sig"] = ""

if "cur_info_images" not in st.session_state:
    st.session_state["cur_info_images"] = []
if "last_info_sig" not in st.session_state:
    st.session_state["last_info_sig"] = ""


# ==========================================
# [초경량 이미지 리사이징 & 자연 정렬 헬퍼]
# ==========================================
def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def process_to_light_image(file_bytes, max_dim=500):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


# ==========================================
# [스마트에디터 표/텍스트 복사 컴포넌트]
# ==========================================
def render_smart_copy_button(text_to_copy, button_label="📋 복사", is_table=False, table_data=None):
    if is_table and table_data:
        tsv_lines = [f"{row.get('항목', '')}\t{row.get('내용', '')}" for row in table_data]
        content = "\\n".join(tsv_lines).replace('"', '\\"').replace("'", "\\'").replace("\r", "")
        alert_msg = "스마트에디터 연동 표가 복사되었습니다! 본문에 붙여넣기(Ctrl+V)하세요."
        btn_style = "background-color:#03c75a; color:white; border:none; padding:10px 14px; border-radius:6px; cursor:pointer; font-weight:bold; font-size:14px; width:100%; touch-action: manipulation;"
    else:
        content = text_to_copy.replace("#", "").replace("'", "\\'").replace('"', '\\"').replace("\r", "").replace("\n", "\\n")
        alert_msg = "복사되었습니다!"
        btn_style = "background-color:#f1f3f5; color:#333; border:1px solid #ced4da; padding:9px 12px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600; width:100%; touch-action: manipulation;"

    js_code = f"""
    <button onclick="
        const text = '{content}';
        if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(text).then(() => alert('{alert_msg}')).catch(() => fallbackCopy(text));
        }} else {{
            fallbackCopy(text);
        }}
        function fallbackCopy(val) {{
            const ta = document.createElement('textarea');
            ta.value = val;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            alert('{alert_msg}');
        }}
    " style="{btn_style}">
    {button_label}
    </button>
    """
    st.components.v1.html(js_code, height=48)


# ==========================================
# [메인 화면 구성]
# ==========================================
st.title("✍️ 네이버 블로그 맞춤 초안 생성기")
st.caption("ZIP 자연 정렬 & 순서 고정 완벽 지원 | C-Rank & D.I.A+ 최적화")

category = st.selectbox("카테고리", ["책육아", "장난감·교구", "방문한 곳", "물건", "음식"])
item_name = st.text_input("장소 / 상호 / 제품명", placeholder="예: 키즈아이콘 다이소 뽀로로 스티커 놀이북")
target_keyword = st.text_input("필수 키워드 (쉼표 구분)", placeholder="예: 다이소스티커, 스티커북, 두돌아기놀이책")
extra_notes = st.text_area("상세 메모 및 솔직 후기 팁", placeholder="솔직한 장단점, 실제 놀아준 팁 등을 적어주세요.", height=70)

st.write("---")

# 1. 정보 사진 등록
st.markdown("#### 📌 메뉴판 / 스펙 / 정보 사진 (선택)")
info_files = st.file_uploader(
    "정보 사진 선택 (또는 ZIP 파일)", 
    type=["jpg", "jpeg", "png", "webp", "zip"],
    accept_multiple_files=True,
    key="uploader_info"
)

if info_files:
    info_sig = "".join([f.name + str(f.size) for f in info_files])
    if st.session_state["last_info_sig"] != info_sig:
        st.session_state["last_info_sig"] = info_sig
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
st.info("💡 **갤러리에서 여러 장을 ZIP으로 압축**해서 올리면 한 번에 쏙 들어옵니다!")

body_files = st.file_uploader(
    "본문 사진 선택 (사진 개별 선택 또는 ZIP 압축 파일)", 
    type=["jpg", "jpeg", "png", "webp", "zip"],
    accept_multiple_files=True,
    key="uploader_body"
)

# 파일이 새로 바뀌었을 때만 파싱 (순서 변경 시 덮어쓰기 방지!)
if body_files:
    body_sig = "".join([f.name + str(f.size) for f in body_files])
    if st.session_state["last_body_sig"] != body_sig:
        st.session_state["last_body_sig"] = body_sig
        new_loaded = []
        with st.spinner("📦 사진 최적화 중..."):
            for f in body_files:
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
                                        new_loaded.append(im)
                    except Exception as e:
                        st.error(f"압축 파일 해제 오류: {e}")
                else:
                    im = process_to_light_image(b)
                    if im:
                        new_loaded.append(im)
            st.session_state["cur_body_images"] = new_loaded

# 본문 사진 순서 조정 및 미리보기 영역 (맨앞/앞/뒤/맨뒤/삭제)
if st.session_state["cur_body_images"]:
    img_list = st.session_state["cur_body_images"]
    
    col_t1, col_t2 = st.columns([3, 1])
    col_t1.success(f"🎉 총 {len(img_list)}장의 사진이 등록되었습니다! 순서를 확인해 주세요.")
    if col_t2.button("🗑️ 사진 전체 삭제"):
        st.session_state["cur_body_images"] = []
        st.session_state["last_body_sig"] = ""
        st.rerun()

    for i in range(0, len(img_list), 2):
        cols = st.columns(2)
        for j in range(2):
            idx = i + j
            if idx < len(img_list):
                with cols[j]:
                    st.image(img_list[idx], caption=f"사진 {idx+1}{' (⭐ 대표 썸네일)' if idx == 0 else ''}", use_container_width=True)
                    
                    b_cols = st.columns(5)
                    # 맨앞으로
                    if idx > 0:
                        if b_cols[0].button("⏮️맨앞", key=f"first_{idx}", help="1번 대표 사진으로 이동"):
                            item = img_list.pop(idx)
                            img_list.insert(0, item)
                            st.rerun()
                    # 한칸 앞으로
                    if idx > 0:
                        if b_cols[1].button("⬅️앞", key=f"up_{idx}"):
                            img_list[idx - 1], img_list[idx] = img_list[idx], img_list[idx - 1]
                            st.rerun()
                    # 한칸 뒤로
                    if idx < len(img_list) - 1:
                        if b_cols[2].button("➡️뒤", key=f"down_{idx}"):
                            img_list[idx + 1], img_list[idx] = img_list[idx], img_list[idx + 1]
                            st.rerun()
                    # 맨뒤로
                    if idx < len(img_list) - 1:
                        if b_cols[3].button("⏭️맨뒤", key=f"last_{idx}", help="마지막 사진으로 이동"):
                            item = img_list.pop(idx)
                            img_list.append(item)
                            st.rerun()
                    # 삭제
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

    with st.spinner("⚡ 사진 분석 및 맞춤 본문 작성 중... (약 15~20초)"):
        pasted_images = st.session_state["cur_body_images"]
        info_images = st.session_state["cur_info_images"]

        system_instruction = """
너는 네이버 블로그 스마트블록 및 VIEW 상위 노출(C-Rank & D.I.A+)을 전문으로 하는 인기 육아/생활 블로거야.
네이버 스마트에디터 ONE의 실제 작성 루틴에 맞춰 결과를 JSON으로 응답해.

[문체 및 작성 원칙 - 절대 엄수]
1. [절대 금지 어휘]: '제격이에요', '쏙쏙 골라', '시선을 사로잡는', '고사리손', '이해가 쏙쏙 돼요', '취향 저격', '꿀템', '대박', '침 꼴깍 삼키며', '큰 축복 같아요', '다채로운', '소장 가치', '~했답니다', '~느낌이었답니다'
2. [문미 변형 규칙]: '눈을 떼지 못해요' -> '눈을 떼지 못하더라구요'로 서술. 담백한 문미('~했어요', '~하더라구요', '~하기 수월해요') 위주 사용.
3. [태그 # 제거]: '#' 기호 절대 금지. 순수 단어로만 작성.
4. [표 규격]: [{"항목": "...", "내용": "..."}] JSON 리스트로 응답. 총평 표는 각 항목당 2~3줄 이상 풍성하게 작성.
5. [본문 스타일]: 옵션 분리 없이, 사실 기반 설명과 실제 경험/반응을 가장 매끄럽게 결합한 단일 완성형 본문(content)으로 작성할 것.

반드시 아래 JSON 포맷으로만 응답:
{
  "titles": ["1안 제목", "2안 제목", "3안 제목"],
  "main_image_tags": "대표사진 하단 태그 단어 3~4개 (# 없이 띄어쓰기 구분)",
  "intro_description": "메인 썸네일 아래 들어갈 2~3줄의 방문/구매/시작 계기와 첫인상",
  "qna_questions": [
    "질문 1",
    "질문 2",
    "질문 3"
  ],
  "intro_bridge": "저도 접해보기 전에 가장 궁금했던 부분들인데, 직접 경험해보고 느낀 점들을 솔직하게 공유해 드릴게요!",
  "summary_table_data": [
    {"항목": "상호/제품명", "내용": "구체적인 이름"},
    {"항목": "종류/구성", "내용": "상세 구성 내용"},
    {"항목": "권장연령/규격", "내용": "대상 연령 및 사이즈"},
    {"항목": "가격/스펙", "내용": "가격 및 주요 스펙"},
    {"항목": "구매처/팁", "내용": "구매처 및 팁"}
  ],
  "body_sections": [
    {
      "photo_index": 2,
      "subheading": "스마트에디터용 소제목 (필요 시 작성, 없으면 빈 문자열)",
      "tags": "사진 하단 태그 2~3단어 (# 제외)",
      "content": "해당 사진에 딱 맞춘 단일 완성형 본문 (직관적인 사실 설명과 솔직한 반응을 담아 3~4줄 내외로 충실하게 작성)"
    }
  ],
  "review_table_data": [
    {"항목": "장점", "내용": "솔직한 장점 2~3줄"},
    {"항목": "아쉬운 점", "내용": "솔직한 아쉬운 점 2~3줄"},
    {"항목": "구매/방문 꿀팁", "내용": "알아두면 유용한 팁 2~3줄"},
    {"항목": "추천 대상", "내용": "어떤 분들에게 잘 맞는지 2~3줄"}
  ],
  "closing_remarks": "마무리 총평 및 소감 2줄",
  "naver_tags": ["태그1", "태그2", "태그3", "태그4", "태그5", "태그6", "태그7", "태그8", "태그9", "태그10"]
}
"""

        prompt_text = f"""
[카테고리]: {category}
[장소/상호/교구/제품명]: {item_name}
[필수 핵심 키워드]: {target_keyword}
[상세 메모 및 솔직 후기 팁]: {extra_notes if extra_notes else "사진 디테일을 보고 솔직하고 구체적인 경험담 위주로 작성해줘"}

등록된 정보 사진 {len(info_images)}장과 본문 사진 {len(pasted_images)}장을 분석해서 포스팅을 작성해줘.
본문 사진 2번부터 {len(pasted_images)}번까지 각 사진에 해당하는 body_sections를 빠짐없이 순서대로 작성해줘.
순수 JSON 형식으로만 응답해줘.
"""

        payload = [prompt_text]
        if info_images:
            payload.append("\n[메뉴판/정보 사진]:\n")
            payload.extend(info_images)
        payload.append("\n[본문 포스팅 사진]:\n")
        payload.extend(pasted_images)

        res = None
        for attempt in range(2):
            try:
                res = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=payload,
                    config=dict(
                        system_instruction=system_instruction,
                        response_mime_type="application/json"
                    )
                )
                break
            except Exception as e:
                err_str = str(e)
                if "503" in err_str and attempt == 0:
                    time.sleep(3)
                    continue
                else:
                    st.error(f"오류가 발생했습니다: {e}")
                    break

        if res and res.text:
            try:
                raw = res.text.strip()
                if raw.startswith("```json"):
                    raw = raw[7:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                data = json.loads(raw.strip())
                st.session_state["result_data"] = data
            except Exception as e:
                st.error(f"결과 데이터 파싱 오류: {e}")

# ==========================================
# [결과 출력 리스트 렌더링 - 1:1 사진 카드 구조]
# ==========================================
if "result_data" in st.session_state:
    data = st.session_state["result_data"]
    
    st.success("🎉 작성이 완료되었습니다! 각 카드의 복사 버튼을 눌러 스마트에디터에 바로 붙여넣으세요.")
    
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
            render_smart_copy_button("", button_label="📊 요약 표 복사 (스마트에디터 붙여넣기 시 표 자동 변환)", is_table=True, table_data=s_table)

    with st.expander("✍️ 사진별 소제목 & 본문 내용 (단일 완성형)", expanded=True):
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
            render_smart_copy_button("", button_label="📊 총평 표 복사 (스마트에디터 붙여넣기 시 표 자동 변환)", is_table=True, table_data=r_table)
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
