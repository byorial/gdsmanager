### 구드공 바로보기 관리(gdsmanager) 플러그인
---

#### 개요
* 구드공 바로보기 경로내 변경사항을 조회하여 Plex에 반영하는 플러그인

---

#### 주요 기능
* 구드공 폴더 감시 및 변경사항 조회
* 마운트 캐시 갱신 및 스케쥴링 전송
* rclone.conf 기반 구드공 및 구글드라이브 탐색 및 웹 재생
---

#### 의존성 및 동작 조건
* lib_gdrive 플러그인 설치 필요: [Gdrive라이브러리 설치](https://sjva.me/bbs/board.php?bo_table=sjva_plugin&wr_id=2096)
* SJVA에 Plex 설정 및 연동
* PMS(Plex Media Server)에 SJVA.bundle 최신버전 설치: [SJVA.bundle](https://github.com/soju6jan/SJVA.bundle)
* PMS에 이치로님 mod버전 rclone 설치 및 구드공드라이브 마운트: [이치로님 rclone-mod](https://sjva.me/bbs/board.php?bo_table=manual&wr_id=5035)
    * 플러그인에서 마운트 캐시 갱신을 위해 마운트시 rc 관련 설정 추가 필요
* SJVA에 rclone 설치필요: tool은 자동설치됨, 아닌경우 도커 컨테이너 내부에 rclone 설치
---

#### 적용 순서
1. PMS에 이치로님 rclone mod 버전 설치 및 구드공 드라이브 마운트
2. PMS에 구드공 자료를 라이브러리에 등록
3. 본 플러그인 설치 및 설정
---

#### 작동 방식 설명
##### 변경사항의 감지
* lib_gdrive 를 통해 Google Drive API 를 사용하여 변경사항을 조회함
* 바로가기 폴더의 경우 Gdrive API 를 통한 변경사항 감지가 불가능함
* 따라서 감시대상 폴더와 서브폴더들을 미리 등록해두고, 해당폴더들의 자식폴더/파일을의 변경사항을 조회함
* 대상폴더가 많아지는 경우 API 호출을 최소화 하기 위해 대상폴더를 50개씩 그룹화하여 조회함   
``` * 구드공바로보기 사용자들은 프로젝트를 공유하므로 사용자가 많은 경우 API 호출수초과 등 오류발생```

---

#### 사용방법
##### 설정: GDS
* 스케쥴링 관련 3인방 - 설명생략
* 스케쥴링 실행정보: 기본값은 30분 입니다. 
* 너무 작은 값으로 입력시 API사용량초과로 전체 구드공바로보기    
    사용자의 서비스에 영향을 미칠 수 있으므로 ***30분 이상***으로 권장합니다. 
* 구드공 리모트명: rclone.conf 에 없는 적당한 이름 입력해주시면 됩니다. 
* 구드공 마운트경로: Plex 서버에 마운트된 구드공 경로 
* RC 관련 설정: vfs/refresh 용도 구드공 마운트시 지정한 rc 설정에 따라 입력(인증은 사용시에만 On)


##### 설정: 제외경로
* 변경사항 감지시 제외할 경로
* 일반적으로 감시대상을 세세하게 지정한 경우 필요 없으나 감시폴더 하위에 제외대상 폴더가 있는 경우 지정 필요 
* 제외할 폴더만 지정하며 fnmatch를 사용하여 비교함
* 설정예제, 보통rclone mount 시 등록한 filter 중 제외필터(-)에 - 를 제거하고 경로만 입력하면됨

##### 설정: 기타
* Chunk 크기: 웹에서 배로 재생시 사용하는 한번에 받아올 데이터 사이즈
* DIR캐시크기: 한번 탐색한 경로는 캐시에 저장함, 메모리 용량에 따라 적당히 할당   (계산은 안해봤으나 대충 10000개쯤 써도 됨)
* 구드공 사용자 인증: 플러그인 로드시 인증하나 로딩시 sjva.me 접속 오류등 예외상황시에만 사용 
* 스캔완료시 알림 전송: Plex스캔 완료시 알림 전송여부, 설정은 시스템 - 설정 - 알림 - 고급에서 해줘야함 


#### 탐색
* 구글드라이브 탐색 및 재생 기능 수행
* 상단 버튼: 검색(캐시리턴), 새로고침(현재 경로 캐시무시 새로고침), 캐시삭제(모등 DIR캐시 삭제)
* 구드공 리모트의 경우에만 마우스 우클릭을 통해 Context 메뉴로 추가기능 처리 가능 
![컨텍스트메뉴](https://cdn.discordapp.com/attachments/845172443214774292/865936718763130914/e51f15dc3c3f2913dab7b911b22c5132_1626096373_9683.png)
* 감시대상에 추가 : 대상 폴더를 감시대상에 추가(상세설명 하단 참조)
* 경로복사: 구드공 바로보기 고침표 등을 위한 파일/폴더 경로를 클립보드에 복사해 주는 기능
* 마운트캐시갱신: 해당 경로의 마운트 캐시 갱신 요청(recursive)
* 스캔명령전송: 해당 경로에 대한 Plex 스캔 요청 전송 

##### 감시대상 등록 관련 설명 
![감시대상등록](https://cdn.discordapp.com/attachments/845172443214774292/865938885216436224/e51f15dc3c3f2913dab7b911b22c5132_1626096613_1834.png)

* 탐색깊이 관련 특이사항
  - 기본: 지정한 경로 + 깊이 폴더의 변경사항 조회함(작품레벨까지만 조회)   
    ex) 영화/1.제목 폴더를 감시대상에 등록시 탐색깊이는 2로 지정 -> 영화/1.제목/가/새로운영화 (2021) 을 검사 
  - 방영중인 show의 경우: 에피소드 변경까지 조회    
    ex) VOD/1.방송중/드라마 폴더를 감시대상에 등록시 탐색깊이는 2로 지정 -> VOD/1.방송중/드라마/드라마A/드라마A.EP1 까지 검사

#### 감시대상 목록
* 감시대상에 지정한 목록을 보여주고 관리함 
* 수정: 감시대상 수정 - 탐색깊이/미디어 유형을 변경하는 경우 하위폴더 정보를 다음 스케쥴링때 다시 불러옴
* 1회실행: 대상경로 검사를 1회 실행(스케쥴링과 기능적으로 동일함)   
  ```스케쥴링시 갱신시각을 갱신하며 그 이후 업데이트된 파일만 조회함, 최초실행시 등록시간 기준이므로 라이브러리 구성 이후 등록 추천```
![감시대상목록](https://cdn.discordapp.com/attachments/845172443214774292/865940030718214154/thumb-e51f15dc3c3f2913dab7b911b22c5132_1626097423_9331_835x299.png)

#### 스캔목록
* 스캔 명령을 전송한 내역을 보여줌 
![스캔목록](https://cdn.discordapp.com/attachments/845172443214774292/865940458625695754/thumb-e51f15dc3c3f2913dab7b911b22c5132_1626097719_6542_835x306.png)
  - 감시대상/스캔목록에서 경로 클릭시 해당경로의 탐색탭으로 이동됨 

#### 한계 
* 영화의 경우 영화작품폴더 하위에 다른버전의 동일 작품이 추가되는 경우 자동감지 안됨   
  여기까지하기엔 변경사항 조회 대상폴더가 너무 많아 져서 효율이 떨어져서 제외함
* 삭제감지불가: 영화작품 폴더가 오류 수정 등으로 다른 폴더로 이동하는 경우 이동된 경우    
  이동된 폴더가 감시대상 인 경우 새로운 작품으로 추가되지만       
  기존폴더의 경우 스캔 후 휴지통 비워줘야함

