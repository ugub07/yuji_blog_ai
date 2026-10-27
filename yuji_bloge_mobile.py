import io
import json
import os
import re
import warnings
import google.genai as genai
from PIL import Image
import streamlit as st

# 경고창 숨김
warnings.filterwarnings("ignore")
os.environ["GRPC_VERBOSITY"] = "ERROR"

# ==========================================
# [페이지 설정 및 비밀번호 보안]
# ==========================================
st.set_page_config(
    page_title="블로그 맞춤 초안 생성기", page_icon="✍️", layout="wide"
)

MY_PASSWORD = "1234"  # 👈 여기서 원하는 비밀번호로 바꿔줘!

if "authenticated" not in st.session_state:
  st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
  st.title("🔒 블로그 생성기 로그인")
  pwd = st.text_input("접속 비밀번호를 입력하세요", type="password")
  if st.button("접속하기"):
    if pwd == MY_PASSWORD:
      st.session_state["authenticated"] = True
      st.rerun()
    else:
      st.error("비밀번호가 일치하지 않습니다.")
  st.stop()

# ==========================================
# [설정 및 API 키]
# ==========================================
GEMINI_API_KEY = "AQ.Ab8RN6L0BRlxvtCiCV8AAE6HPgjsuqLn6UWIcQXN0_7So3THuA"
client = genai.Client(api_key=GEMINI_API_KEY)


# ==========================================
# [웹/모바일 클립보드 복사 컴포넌트]
# ==========================================
def render_copy_button(
    text_to_copy, button_label="📋 복사", is_table=False, table_data=None
):
  """웹 브라우저 환경에서 원클릭으로 텍스트 및 네이버 표를 클립보드에 넣는 자바스크립트 헬퍼"""
  if is_table and table_data:
    # 네이버 스마트에디터 친화 탭 구분 텍스트(TSV) 생성
    tsv_lines = [
        f"{row.get('항목', '')}\t{row.get('내용', '')}" for row in table_data
    ]
    tsv_content = (
        "\\n".join(tsv_lines).replace('"', '\\"').replace("'", "\\'")
    )
    js_code = f"""
        <button onclick="navigator.clipboard.writeText('{tsv_content}').then(() => alert('표가 복사되었습니다! 네이버 에디터에 Ctrl+V 하세요.'));" 
        style="background-color:#03c75a; color:white; border:none; padding:8px 14px; border-radius:6px; cursor:pointer; font-weight:bold; font-size:14px; width:100%;">
        {button_label}
        </button>
        """
  else:
    # 순수 텍스트 정제
    clean_txt = (
        text_to_copy.replace("#", "")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )
    js_code = f"""
        <button onclick="navigator.clipboard.writeText('{clean_txt}').then(() => alert('복사되었습니다!'));" 
        style="background-color:#f1f3f5; color:#333; border:1px solid #ced4da; padding:7px 12px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:600; width:100%;">
        {button_label}
        </button>
        """
  st.components.v1.html(js_code, height=45)


# ==========================================
# [메인 화면 구성]
# ==========================================
st.title("✍️ 네이버 블로그 맞춤 초안 생성기")
st.caption("갤탭 & 모바일 완벽 지원 | C-Rank & D.I.A+ 최적화")

col1, col2 = st.columns([1, 1])

with col1:
  category = st.selectbox(
      "카테고리", ["책육아", "장난감·교구", "방문한 곳", "물건", "음식"]
  )
  item_name = st.text_input("장소 / 상호 / 제품명", placeholder="예: 키즈아이콘 다이소 뽀로로 스티커 놀이북")
  target_keyword = st.text_input("필수 키워드 (쉼표 구분)", placeholder="예: 다이소스티커, 스티커북, 두돌아기놀이책")
  extra_notes = st.text_area(
      "상세 메모 및 솔직 후기 팁",
      placeholder="솔직한 장단점, 실제 놀아준 팁 등을 적어주세요.",
      height=80,
  )

with col2:
  info_files = st.file_uploader(
      "📌 메뉴판 / 스펙 / 정보 사진 (선택 사항)",
      type=["png", "jpg", "jpeg", "webp"],
      accept_multiple_files=True,
  )
  body_files = st.file_uploader(
      "📷 본문 사진 (필수: 갤러리에서 여러 장 선택 가능)",
      type=["png", "jpg", "jpeg", "webp"],
      accept_multiple_files=True,
  )

st.divider()

if st.button("✨ 블로그 맞춤 초안 생성하기", type="primary", use_container_width=True):
  if not item_name or not target_keyword:
    st.warning("명칭과 필수 키워드를 입력해 주세요!")
    st.stop()

  if not body_files:
    st.warning("본문 사진을 최소 1장 이상 등록해 주세요!")
    st.stop()

  with st.spinner("⚡ 네이버 최적화 초안 작성 중... (약 15~25초 소요)"):
    # 이미지 파싱
    pasted_images = [Image.open(f) for f in body_files]
    info_images = [Image.open(f) for f in info_files] if info_files else []

    system_instruction = """
        너는 네이버 블로그 스마트블록 및 VIEW 상위 노출(C-Rank & D.I.A+)을 전문으로 하는 파워 육아/생활 블로거야.
        실제 네이버 스마트에디터 작성 루틴에 맞춰 JSON으로 응답해.

        [문체 및 작성 원칙 - 절대 금지 및 변형 규칙]
        1. [절대 금지 어휘]: '제격이에요', '쏙쏙 골라', '시선을 사로잡는', '고사리손', '이해가 쏙쏙 돼요', '취향 저격', '꿀템', '대박', '침 꼴깍 삼키며', '큰 축복 같아요', '다채로운', '소장 가치', '~했답니다', '~느낌이었답니다'
        2. [문미 변형 규칙]: '눈을 떼지 못해요' -> '눈을 떼지 못하더라구요'로 서술. 담백한 문미('~했어요', '~하더라구요', '~하기 수월해요') 위주 사용.
        3. [태그 # 제거]: '#' 기호 절대 금지. 순수 단어로만 작성.
        4. [표 규격]: [{"항목": "...", "내용": "..."}] JSON 리스트로 응답. 총평 표는 빈약하지 않게 각 항목당 2~3줄 이상 풍성하게 작성.

        반드시 아래 JSON 포맷으로만 응답할 것:
        {
          "titles": ["1안 제목", "2안 제목", "3안 제목"],
          "main_image_tags": "대표사진 하단 태그 단어 3~4개 (# 없이 공백 구분)",
          "intro_description": "메인 썸네일 아래 들어갈 2~3줄의 방문/구매/시작 계기와 첫인상",
          "qna_questions": [
            "질문 1",
            "질문 2",
            "질문 3"
          ],
          "intro_bridge": "저도 들이기(구매하기) 전에 가장 궁금했던 부분들인데, 직접 경험해보고 느낀 점들을 솔직하게 공유해 드릴게요!",
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
              "option_1": "1번 스타일 본문 설명 (담백하고 직관적인 사실 설명 2~3줄)",
              "option_2": "2번 스타일 본문 설명 (실제 아이 반응이나 디테일 2~3줄)"
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
        본문 사진 2번부터 {len(pasted_images)}번까지 각 사진에 해당하는 body_sections를 빠짐없이 작성해줘.
        JSON 포맷으로만 응답해줘.
        """

    payload = [prompt_text]
    if info_images:
      payload.append("\n[메뉴판/정보 사진]:\n")
      payload.extend(info_images)
    payload.append("\n[본문 포스팅 사진]:\n")
    payload.extend(pasted_images)

    try:
      res = client.models.generate_content(
          model="gemini-3.8-flash",
          contents=payload,
          config=dict(
              system_instruction=system_instruction,
              response_mime_type="application/json",
          ),
      )
      raw = res.text.strip()
      if raw.startswith("```json"):
        raw = raw[7:]
      if raw.endswith("```"):
        raw = raw[:-3]
      data = json.loads(raw.strip())
      st.session_state["result_data"] = data
    except Exception as e:
      st.error(f"생성 중 오류가 발생했습니다: {e}")

# ==========================================
# [결과 출력 리스트 렌더링]
# ==========================================
if "result_data" in st.session_state:
  data = st.session_state["result_data"]

  st.success("🎉 글 생성이 완료되었습니다! 순서대로 복사해서 사용하세요.")

  # 1. 제목 영역
  with st.expander("📌 네이버 추천 제목 (선택 복사)", expanded=True):
    for i, t in enumerate(data.get("titles", []), start=1):
      c_txt, c_btn = st.columns([5, 1])
      c_txt.markdown(f"**{i}안:** {t}")
      with c_btn:
        render_copy_button(t, f"{i}안 복사")

  # 2. 사진 1 도입부
  with st.expander("📷 사진 1 (대표 썸네일) 아래 구성 요소", expanded=True):
    tag_main = data.get("main_image_tags", "")
    if tag_main:
      c1, c2 = st.columns([5, 1])
      c1.markdown(f"**사진 1 태그:** `{tag_main}`")
      with c2:
        render_copy_button(tag_main, "태그 복사")

    intro_desc = data.get("intro_description", "")
    c1, c2 = st.columns([5, 1])
    c1.markdown(f"**[도입 설명]**\n\n{intro_desc}")
    with c2:
      render_copy_button(intro_desc, "설명글 복사")

    qna_list = data.get("qna_questions", [])
    qna_txt = (
        "\n".join(qna_list) if isinstance(qna_list, list) else str(qna_list)
    )
    c1, c2 = st.columns([5, 1])
    c1.info(f"**[Q&A 질문 - 인용구용]**\n\n{qna_txt}")
    with c2:
      render_copy_button(qna_txt, "인용구 복사")

    intro_bridge = data.get("intro_bridge", "")
    c1, c2 = st.columns([5, 1])
    c1.markdown(f"**[연결 본문글]**\n\n{intro_bridge}")
    with c2:
      render_copy_button(intro_bridge, "연결글 복사")

  # 3. 상단 요약 표
  with st.expander("📊 상단 요약 표 (스마트에디터 표 연동)", expanded=True):
    s_table = data.get("summary_table_data", [])
    if s_table:
      for r in s_table:
        st.markdown(f"• **{r.get('항목')}**: {r.get('내용')}")
      render_copy_button(
          "",
          button_label="📊 요약 표 복사 (스마트에디터 표 자동 변환)",
          is_table=True,
          table_data=s_table,
      )

  # 4. 본문 사진별 카드
  with st.expander("✍️ 사진별 소제목 & 본문 내용", expanded=True):
    for sec in data.get("body_sections", []):
      p_idx = sec.get("photo_index", 2)
      st.markdown(f"#### 📷 본문 사진 {p_idx}")

      sub = sec.get("subheading", "")
      if sub:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**소제목:** {sub}")
        with c2:
          render_copy_button(sub, "소제목 복사")

      p_tag = sec.get("tags", "")
      if p_tag:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**사진 태그:** `{p_tag}`")
        with c2:
          render_copy_button(p_tag, "태그 복사")

      opt1 = sec.get("option_1", "")
      if opt1:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**옵션 1 (설명형):**\n\n{opt1}")
        with c2:
          render_copy_button(opt1, "1번 본문 복사")

      opt2 = sec.get("option_2", "")
      if opt2:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**옵션 2 (감성/반응형):**\n\n{opt2}")
        with c2:
          render_copy_button(opt2, "2번 본문 복사")
      st.divider()

  # 5. 하단 총평 표
  with st.expander("📊 하단 총평 표 & 솔직 후기 마무리", expanded=True):
    r_table = data.get("review_table_data", [])
    if r_table:
      for r in r_table:
        st.markdown(f"• **{r.get('항목')}**: {r.get('내용')}")
      render_copy_button(
          "",
          button_label="📊 총평 표 복사 (스마트에디터 표 자동 변환)",
          is_table=True,
          table_data=r_table,
      )

    closing = data.get("closing_remarks", "")
    if closing:
      c1, c2 = st.columns([5, 1])
      c1.markdown(f"**[마무리 소감]**\n\n{closing}")
      with c2:
        render_copy_button(closing, "소감 복사")

  # 6. 네이버 태그
  with st.expander("🏷️ 네이버 스마트에디터 등록용 태그", expanded=True):
    tags = data.get("naver_tags", [])
    if tags:
      comma_tags = ", ".join([t.replace("#", "").strip() for t in tags])
      c1, c2 = st.columns([5, 1])
      c1.code(comma_tags)
      with c2:
        render_copy_button(comma_tags, "태그 전체 복사 (쉼표 구분)")
