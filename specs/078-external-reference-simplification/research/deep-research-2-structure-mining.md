# 미지 레거시 텍스트 형식의 구조 추론·의미 청킹: 결정론 알고리즘 + 약한 LLM 최적 결합 연구

## TL;DR

- **핵심 결론:** 1순위 아키텍처는 "결정론 템플릿 마이닝(Drain의 **고정 깊이 파스 트리** 원리) → LLM은 명명·경계 확정만 → 결정론 검증기 불변"의 3단 하이브리드다. **단, Drain3나 logpai/logparser 같은 기성 라이브러리를 청킹 엔진으로 그대로 쓰면 안 된다** — 이들은 입력 줄을 `strip()`+공백 토큰화로 정규화하고 출력에 원문 byte/char offset을 전혀 담지 않아, 우리의 `chunk.text == decoded_source[char_start:char_end]` 불변식과 구조적으로 양립 불가하기 때문이다. **알고리즘 원리만 채용하고 offset을 보존하는 자체 구현**이 유일한 해법이다.
- **최신 연구의 수렴:** LogParser-LLM(KDD'24)·DeepParse(EASE'26)·LibreLog(ICSE'25)·LILAC(FSE'24)가 정확히 "결정론이 후보 구조를 만들고 LLM은 신규/애매 케이스만 확정" 분업으로 수렴했고, LibreLog는 온프레미스 소형 오픈모델(Llama3-8B)만으로 LogHub-2.0 SOTA를 실측했다 — 우리의 GPU 1대·클라우드 불가 제약과 정확히 양립한다.
- **평가:** LogHub-2.0의 GA/PA/FGA/FTA 지표(특히 희귀 레코드에 강건한 FGA/FTA)를 gold corpus에 차용하되, 우리 고유의 **span-exactness 지표**(byte-exact 일치율)와 decoy 오매치율을 추가로 정의해야 한다.

## Key Findings

1. **span(offset) 복원 가능성이 알고리즘 선택의 결정 기준이며, 기성 로그 파서는 모두 실패한다.** logpai/logparser의 Drain/Spell/IPLoM/AEL은 로드 시 `regex.search(line.strip())`으로 줄을 stripping하고, 알고리즘은 `.strip().split()`으로 공백 토큰화하며(IPLoM은 추가로 `[\s=:,]`로 분할), 출력은 `*_templates.csv`(EventId·EventTemplate·Occurrences)와 `*_structured.csv`(LineId·Content·EventTemplate·ParameterList)뿐 — **byte/char offset 컬럼이 없다.** Drain3 역시 `add_log_message()`가 클러스터 ID/템플릿만 반환하고 파라미터 추출은 정규식 재적용 방식이어서 원문 스팬을 돌려주지 않는다. 따라서 이들의 산출물은 "재직렬화된 텍스트"이지 "원문 스팬"이 아니다.

2. **Drain 원리 자체는 최선의 결정론 후보다.** He·Zhu·Zheng·Lyu의 원논문("Drain: An Online Log Parsing Approach with Fixed Depth Tree", IEEE ICWS 2017, pp.33–40)에서 Drain은 HDFS 0.998, BGL 0.963, ThunderBird 0.955의 정확도와 O(n) 선형 시간을 보이며, 5개 실데이터셋에서 기존 대비 최소 51.85% 개선·HPC 실행시간 81.47% 감소를 보고했다. 온라인·결정론·저오버헤드라는 성질이 우리 용도에 맞는다.

3. **LLM+결정론 분업 패턴이 학계에서 검증되었다.** LogParser-LLM(Zhong 외, KDD 2024, arXiv:2408.13727)은 "Drain에서 착안한 prefix parse tree"로 매칭하고 LLM은 ICL+NER 프롬프트로 **신규 템플릿 추출만** 담당한다. DeepParse(Shetaia & Kauffman, EASE 2026, arXiv:2604.20553)는 한 발 더 나아가 **LLM을 설치 시 1회 offline 실행**해 regex 마스크를 합성하고, runtime 파싱은 전적으로 Drain(결정론)에 맡긴다. 이는 우리가 지향하는 "결정론이 후보, LLM은 확정, skill 파일로 재사용"과 동형이다.

4. **온프레미스 소형 오픈모델로 충분하다.** LibreLog/OpenLogParser(Ma·Kim·Chen, ICSE 2025, arXiv:2408.01585)는 Llama3-8B(Meta-Llama-3-8B-Instruct)만으로 LogHub-2.0에서 평균 GA 87.2, PA 85.4, FGA 82.3, FTA 65.1을 달성해 전 지표 SOTA를 기록했다(논문: "LibreLog achieved the highest performance, with an average GA of 87.2, PA of 85.4, FGA of 82.3, and FTA of 65.1, surpassing all other parsers"; PA·FTA는 기존 LLM 파서 대비 각 +13.7%, +6.9%). 클라우드·상용 API 없이 프라이버시·비용 문제를 해소한다.

5. **PBE 계열(PROSE/PADS)은 우리 제약에 부적합하다.** Microsoft PROSE/FlashExtract는 비상업용 전용 라이선스이고 .NET 4.5+/.NET Core에 의존하며 공식 Python 바인딩이 없다. PADS/LearnPADS는 C/ML/Haskell 구현으로 Python 생태계 밖이다. 둘 다 "Windows + Python 3.12 단일 프로세스" 및 상용 제품 요건과 충돌한다.

## Details

### 1. 결정론 템플릿 마이닝 / 로그 파서 비교표

| 알고리즘/라이브러리 | 원리 | 정확도(LogHub, 출처) | 멀티라인 | span(offset) 복원 | 라이선스 | 유지보수(최근) | Windows/Py3.12 |
|---|---|---|---|---|---|---|---|
| **Drain** (He 2017, ICWS) | 고정 깊이 파스 트리, 첫 토큰·길이로 분기 후 유사도 매칭, O(n) | HDFS 0.998, BGL 0.963, ThunderBird 0.955 (원논문) | ✗ 줄단위 | ✗ 원리상 토큰화로 미지원 | 원논문(구현 아님) | — | — |
| **Drain3** (logpai/IBM) | Drain + 마스킹 + 영속화 + 파라미터 추출 | Drain과 동일 원리 | ✗ 줄단위 | ✗ `add_log_message`는 클러스터/템플릿만 반환, offset 없음 | MIT | logpai 본체 정체(#98 수년간 미해결, PyPI 마지막 릴리스 2022-07), 포크 `drain3-improved`(2026-04 릴리스) 활발 | ✓ pip, 순수 Python |
| **logpai/logparser** (Drain/Spell/IPLoM/AEL 등) | 각 알고리즘 참조 구현 벤치마크 | LogHub 벤치마크 기준값 원출처 | ✗ 줄단위(별도 hybridlogparser 존재) | ✗ `strip()`+공백 토큰화, 출력 templates/structured CSV, offset 없음 | 알고리즘별: Drain/Spell/IPLoM/AEL=Apache-2.0, SLCT·LogCluster=GPL-2.0, LogMine=GPL, LenMa=BSD | 저활동이나 유지(v1.0.4, org "Updated Jun 10, 2025") | ✓ pip `logparser3`, 순수 Python(단 SLCT는 gcc, LogCluster는 perl 필요) |
| **Spell** (Du 2016) | LCS(최장공통부분수열) 기반 스트리밍 | HDFS 1.0, Zookeeper 0.964 | ✗ 줄단위 | ✗ | Apache-2.0(logparser 내) | 위와 동일 | ✓ |
| **IPLoM** (Makanju) | 반복 분할(길이→토큰위치→매핑) | HDFS 1.0, Hadoop 0.954, OpenStack 0.871 | ✗ 줄단위 | ✗ 공백+`=:,` 분할로 더 공격적 정규화 | Apache-2.0 | 위와 동일 | ✓ |
| **AEL** (Jiang 2008) | 휴리스틱 토큰 비교·병합 | HDFS 0.998, HPC 0.903 | ✗ 줄단위 | ✗ | Apache-2.0 | 위와 동일 | ✓ |
| **LogMine** (Hamooni 2016) | 계층 클러스터링 | 데이터셋별 편차 큼 | ✗ | ✗ | GPL(logparser 내 gist 기반) | 저활동 | GPL 주의 |

핵심: 정확도 수치는 모두 **로그 데이터셋 기준**이며 비-로그 레거시 텍스트로의 이전은 재측정 필요. 그러나 **span 미보존은 데이터셋과 무관한 구조적 결격**이므로, 이 표의 어떤 라이브러리도 우리 불변식하에서는 최종 청킹 엔진이 될 수 없다.

### 2. 예제 기반 추출 규칙 합성 (PBE)

| 도구 | 원리 | 오픈 구현 | 라이선스 | .NET 의존 | Python 바인딩 |
|---|---|---|---|---|---|
| **Microsoft PROSE / FlashExtract** | DSL + 예제로부터 프로그램 귀납 합성; FlashExtract는 텍스트 영역 추출 DSL(PLDI 2014) | NuGet `Microsoft.ProgramSynthesis`(샘플만 GitHub 공개, 엔진 비공개) | **비상업용 전용** | ✓ .NET 4.5+/.NET Core 필수 | ✗ 공식 Python 바인딩 없음 |
| **PADS / LearnPADS** | ad hoc 데이터 기술 언어 + 예제로부터 형식 추론(POPL 2007, SIGMOD 2008) | C/ML/Haskell 구현(Tufts/Princeton) | 학술 배포 | — | ✗ |

권고: **둘 다 기각.** 다만 PADS의 개념(형식 기술 언어 + 예제 추론 + "parse metric"으로 파싱 품질 측정: 오류 토큰 수·정상 토큰 수·스킵 문자 수·정상 문자 수)은 우리 검증기 지표 설계에 참고할 가치가 있다.

### 3. LLM + 결정론 하이브리드 최신 연구 (2023–2026)

| 논문(연도/학회) | 결정론이 하는 일 | LLM이 하는 일 | LLM 호출 축소 | 소형/오픈모델 실측 |
|---|---|---|---|---|
| **LILAC** (Jiang 외, FSE'24, arXiv:2310.01796) | adaptive parsing cache(prefix tree)에 템플릿 저장·매칭 | ICL로 신규 로그만 파싱, 캐시 템플릿 적응적 정제 | 캐시 매칭으로 LLM 질의를 수 자릿수 감소, 속도는 Drain에 필적 | Curie(13B)로도 기존 파서 상회; Loghub-2.0에서 grouping/template F1 각 +66.8%, +69.5% |
| **LogParser-LLM** (Zhong 외, KDD'24, arXiv:2408.13727) | Drain 착안 prefix parse tree로 매칭·군집 | 신규 템플릿 추출만(ICL+NER 프롬프트) | tree 매칭으로 LLM 호출 최소화; Granularity Distance 지표 신설 | — |
| **LibreLog / OpenLogParser** (Ma 외, ICSE'25, arXiv:2408.01585) | 고정 깊이 grouping tree로 그룹화 | 그룹 내 템플릿 확정 + self-reflection | template memory로 재사용, LILAC 대비 2.7× 빠름 | **Llama3-8B로 GA 87.2/PA 85.4/FGA 82.3/FTA 65.1 SOTA** |
| **DeepParse** (Shetaia & Kauffman, EASE'26, arXiv:2604.20553) | **Drain가 runtime 결정론 파싱**(동일 입력→동일 템플릿 보장) | **offline 1회** regex 마스크 합성만 | LLM은 설치 시 1회만; per-line 추론 대비 ~100× 빠름(100 logs 0.30s vs LLaMA-7B 28.93s) | DeepSeek-R1:8B, 시스템당 50샘플로 97.6% PA |
| **DivLog** (Xu 외, ICSE'24) | 다양성 최대화 후보 샘플링 | ICL 파싱(5-shot) | — | GPT-3(대형, 175B) |
| **LogPPT** (Le & Zhang, ICSE'23) | 온라인 캐싱 | RoBERTa prompt-tuning으로 키워드/파라미터 인식 | 캐시 | 소형 PLM(RoBERTa-base), 평균 PA 0.916 |

우리 시스템("결정론이 후보 구조 생성, LLM은 명명·경계 확정")과 가장 가까운 것은 **DeepParse**(LLM offline 1회 + Drain runtime)와 **LogParser-LLM**(prefix tree 매칭 + LLM 신규 템플릿만)이다. DeepParse의 "reasoning(LLM, run once) / execution(Drain, run forever) 분리"는 우리의 "skill 파일 저장 후 LLM 없이 재사용"과 사실상 동일 설계다.

### 4. 멀티라인 레코드 경계 검출 (검증된 결정론 기법)

- **헤더 앵커 정규식(가장 신뢰성 높음):** 줄 시작의 반복 패턴(타임스탬프/키/ID)을 새 레코드 시작으로 보고, 다음 앵커 출현 전까지를 한 레코드로 묶는다. Sumo Logic("Infer Boundaries" + Boundary Regex), NXLog(`xm_multiline`의 HeaderLine/FixedLineCount), rsyslog·Datadog 모두 이 방식을 표준으로 채택한다. 라인 시작에 앵커된 타임스탬프가 가장 신뢰성 높은 경계다.
- **들여쓰기 블록:** 부모 줄 + 더 깊은 들여쓰기 연속 줄을 한 레코드로. Hue(arXiv:2308.07085)의 "line aggregating"은 인접 줄의 시퀀스 유사도와 들여쓰기 수로 table log와 text log를 구분한다.
- **빈 줄 구분 / BEGIN-END 구분자:** 명시적 구분자가 있을 때 결정론적으로 신뢰 가능.
- **학술 기법:** LPME(CSSE, 단일 줄 템플릿 결과 위에서 반복 스캔 + 휴리스틱으로 멀티라인 이벤트 식별, 실험 데이터셋에서 recall 1.0·precision >0.9), 그리고 USPTO 9,626,414의 후보 경계 랭킹(날짜는 레코드 시작, free text는 레코드 끝에 오는 경향을 활용). logpai에는 멀티라인 전용 별도 프로젝트 **hybridlogparser**가 존재하며, 이는 전통적 logparser가 단일 줄 전용임을 방증한다.

권고: 경계 검출은 **앵커 정규식 → 들여쓰기 → 빈 줄/구분자** 순으로 시도하고, 어느 것도 확신 임계를 넘지 못하면 **경계를 만들지 않는다(fail-closed)**.

### 5. 평가 방법 및 gold corpus 설계

LogHub-2.0(logpai/loghub-2.0, ISSTA'24; 14개 오픈소스 시스템, 5천만+ 로그, 3,488 템플릿)의 4대 지표를 차용한다:
- **GA (Grouping Accuracy):** 같은 ground-truth 템플릿의 메시지가 함께 그룹핑된 비율(메시지 레벨).
- **PA (Parsing Accuracy):** 각 메시지의 static/variable 토큰이 모두 정확히 식별된 비율.
- **FGA (F1 of Grouping Accuracy):** 정확 그룹핑된 **템플릿** 비율의 F1(PGA·RGA 조화평균). 메시지 불균형에 강건.
- **FTA (F1 of Template Accuracy):** 템플릿 토큰 시퀀스 정확 일치 + 메시지 정확 할당 조건의 F1. 희귀(오류) 로그에 가중.

precision-first인 우리에겐 **FGA/FTA가 GA/PA보다 적합**하다(대다수 메시지가 소수 템플릿에 몰리는 불균형에서 GA/PA는 과대평가됨). 우리 gold corpus에는 여기에 두 지표를 **추가**한다: (1) **span-exactness** = 생성 청크가 `decoded_source[char_start:char_end]`와 byte-exact 일치하는 비율(우리 불변식의 직접 측정), (2) **decoy 오매치율** = 타 형식(XML/YAML/JSON) 파일에 규칙이 잘못 매치하는 비율(0이어야 승격). label-free 지표(예: PMSS, arXiv:2512.21811의 cohesion/separation)는 ground-truth 없는 신규 형식의 예비 스크리닝에 보조 활용 가능하나 승격 근거로는 부적합.

## Recommendations

**단계 0 — 원문 앵커 인덱싱 (신규, 결정론).** 파일을 1회 디코드해 `decoded_source`를 확정하고 각 물리 줄의 `(char_start, char_end)`를 배열로 보관한다. 이후 **모든 청크는 이 offset 배열에서만 파생**하며, 재직렬화·정규화 텍스트는 절대 원문으로 취급하지 않는다. — *벤치마크: span-exactness = 100%가 아니면 이 단계 버그로 간주.*

**단계 1 — 멀티라인 경계 검출 (결정론).** 앵커 정규식 → 들여쓰기 → 빈 줄/BEGIN-END 순으로 시도, 각 경계를 offset으로 표현. 확신 임계 미달 시 경계 생성 안 함. — *벤치마크: held-out 파일에서 경계 재현율이 임계 미만이면 앵커 후보를 재선정.*

**단계 2 — 줄 모양 결정론 군집화 (Drain 원리, 자체 구현).** 각 레코드/줄의 토큰 클래스 시퀀스를 고정 깊이 파스 트리로 군집한다. 토큰화는 **분류 목적에만** 쓰고 원문 substring은 offset으로 항상 보존. 출력은 "이 offset 구간들이 동일 구조 클러스터"라는 후보. LLM 불필요. Drain3(MIT)는 이 단계의 **검토 후보 생성 보조**로만(임베딩·사실생성 금지) 참고.

**단계 3 — LLM 축소 역할 (명명·경계 확정만).** 클러스터별 대표 소수만 온프레미스 소형 모델에 제시(도구: 줄모양 분포 요약·구간 정독·grep). LLM은 (a) 클러스터가 의미 레코드인가 Y/N, (b) 레코드/키 명명, (c) 애매 경계 확정만 수행 — 규칙 발명이 아니라 후보 승인. LILAC식 캐시로 동일 구조 재질의 방지. — *벤치마크: LLM 호출당 승인율과 키 접두어 오류율을 모니터링, 오류율이 임계 초과 시 프롬프트에 decoy 대비 예시 추가.*

**단계 4 — 결정론 검증기 (불변, 변경 없음).** `chunk.text == decoded_source[char_start:char_end]` 강제, held-out 성립 시에만 승격, decoy 오매치 시 실격, 통과분만 skill 파일 저장. LibreLog template memory / DeepParse "설치 1회 합성 → runtime 결정론"과 동형으로, 다음 분석부터 LLM 없이 재사용.

**단계 5 — 평가 상시화.** GA/PA/FGA/FTA + span-exactness + decoy 오매치율을 CI에 편입. FGA/FTA를 1차 품질 게이트로, span-exactness와 decoy 오매치율을 **승격 hard gate**로 사용.

**단계 조정을 바꿀 임계/벤치마크:** (i) 소형 모델의 승인 정밀도가 gold에서 목표치(예: FTA 기준선) 미달이면 → 단계 3을 LogPPT/UNLEASH식 소형 PLM 토큰 분류(GPU 보유 활용, 후보 제안 한정)로 보강 검토. (ii) 뒤에서 구조가 바뀌는 대형 파일에서 grouping이 붕괴하면 → Drain 트리를 파일 구간별로 재학습(온라인 적응)하고 held-out을 구간별로 분리. (iii) 경계 검출이 불안정하면 → 앵커를 만들지 않고 fail-closed(파일 미분석 표기)를 유지, precision을 recall보다 우선.

## Caveats

- **span 미보존은 코드 수준에서 확인**했다(logpai/logparser의 `strip()`+공백 토큰화, IPLoM의 `[\s=:,]` 분할, 출력 CSV에 offset 컬럼 부재; Drain3의 `add_log_message` 반환값). 다만 "멀티라인 미지원"이 logparser README에 직접 문장으로 명시된 것은 **미확인**이며, 별도 프로젝트 hybridlogparser의 존재와 소스의 줄단위 루프로 간접 확인된다.
- LogHub 정확도 수치(Drain HDFS 0.998 등)는 **로그 데이터셋 기준**으로, 우리의 비-로그 레거시 텍스트(인터페이스 정의·설정·배치 정의)에 그대로 이전된다는 보장은 없다. 반드시 자체 gold corpus로 재측정할 것.
- LibreLog의 "Llama3-8B SOTA(GA 87.2 등)"·DeepParse의 "97.6% PA"·LILAC의 "F1 +66.8%/+69.5%"는 **각 논문의 LogHub 기준 저자 보고치**이며, 우리 형식·우리 중형 오픈모델에서의 재현은 미확인. DeepParse는 EASE 2026 채택(camera-ready 미공개) 상태로, 수치는 프리프린트/논문 기준.
- PROSE는 **비상업용 전용** 라이선스여서 상업 제품(레거시 코드 분석 제품)에 사용 불가. logpai/logparser는 GPL 구성요소(SLCT·LogCluster·LogMine)를 포함하므로, 채택 시 **Apache-2.0인 Drain/Spell/IPLoM/AEL 부분만** 선택적으로 참고해야 라이선스 오염을 피한다.
- 소형 오픈모델의 품질 편차(키 접두어 포함, 헤더 행 오인)는 본 아키텍처가 LLM 책임을 "후보 승인"으로 축소해 완화하나 제거하지는 못한다. held-out 검증과 fail-closed가 최후 방어선이며, 불확실하면 청크를 만들지 않는 것이 precision-first 원칙상 정답이다.