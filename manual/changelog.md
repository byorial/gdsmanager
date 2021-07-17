### 업데이트 내역
---
##### v0.1.1.0 (21.07.17) 전체 스캔 로직 개선 
* 이전: n일 전체 스캔시 마운트 캐시는 갱신하지 않음 
* 변경: 전체스캔시 마운트 캐시 갱신 요청(async) 후 갱신완료를 확인(rclone rc job/status활용) 후 스캔명령 전송
* 메타데이터 새로고침 기능
	* 탐색탭에서 특정 작품(폴더)이나 에피소드(파일)에 대한 메타데이터 갱신 요청 기능 추가  
      `※ 영상파일이나 부모폴더만 지원함, 디렉토리 캐시에 저장되어 있어야함`
* 매뉴얼 및 업데이트 내역 플러그인에서 확인 가능 하도록 변경

#### v0.1.0.5 (21.07.16.) 
* 버그수정(버전업X): 쇼 에피 추가시 rclone cache 가 갱신되지 않던 문제 수정

#### v0.1.0.5 (21.07.15.) 
* 기능 추가
	* 마운트 필터 적용: rclone mount시 제외처리한 필터 적용, 설정 - 제외 경로에 입력   
	  입력시 rclone filter의 맨 앞 - 는 제거하고 경로만 입력, 감시대상에서 제외할 경로만 입력   
	  이곳에 입력된 경로는 감시대상 하위에 위치하더라도 스캔대상에서 제외됨
	* 일일전체스캔 -> n일 전체스캔 으로 변경
* 버그수정: 윈도우 Plex의 경우 부모폴더의 plex 경로와 `rc_path`를 잘 못 얻어오는 문제 수정

#### v0.1.0.3 (21.07.15.) 
* 버그수정: 윈도우 Plex의 경우 `rc_path` 관련 오류 수정

#### v0.1.0.3 (21.07.14.) 
* 버그수정: 스캔관련 버그 수정(Plex `section_id`를 못가져와서 실제 스캔이 안되는 문제 수정)

#### v0.1.0.2 (21.07.14.) 
* 버그수정: Plex라이브러리에 등록여부 판단 로직 버그 수정
* 기능추가: 
	* (마운트캐시 갱신 로직 개선): 마운트 경로에 존재하지 않는 부모폴더에 대한 갱신로직 추가

#### v0.1.0.1 (21.07.13.) 
* 버그수정: 1회실행시 버그 수정, 감시폴더 추가시 경로 관련 버그 수정
* 기능추가: 
	* (기준시간 기능추가): 설정 - 기타 - 1회실행시간기준 추가, 1회실행시 조회할 기준시각을 설정, 분단위    
       eX) 0으로 설정시 스케쥴링 동작과 동일, 60 지정시 한시간 전 이후 변경된 파일 조회/처리
	* ~~(일일 전체스캔 기능): 설정 - GDS - 매일전체스캔 기능 추가~~    
       ~~On: 감시대상에 등록된 스케쥴 대상 폴더에 대해 스캔명령 전송함, 매일 00시 이후 첫 실행시 실행됨~~


#### v0.1.0.1 (21.07.12.)
* 플러그인 공개