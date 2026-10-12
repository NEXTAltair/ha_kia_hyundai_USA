# 日本版 Stamp の単体生成

## 背景

PR #2 の実車検証は、別プロセスから公式 Android アプリを呼び出す Stamp サービスを使用した。この方式を常用すると Android・Frida・HTTP サーバーの常時稼働が必要になり、HACS で導入する統合の運用負担が増える。

## 決定

日本版アプリ 1.0.17 で確認したアプリ ID と10桁の Unix 秒を対象に、既存の CFB/XOR 経路で HA 内に署名を生成する。ユーザーが一度用意した47バイトの署名用データを保存し、定期取得では公式アプリを呼ばない。

専用セットアップスクリプトは8つの時刻で公式アプリの出力を照合し、同一の署名用データで再現できる場合だけ秘密ファイルを作成する。秘密値・アプリのバイナリは配布しない。既存の CFB 設定を Options にも追加し、再認証時に保持する。

## 根拠と検証範囲

2026-10-12、日本版公式アプリの8つの出力を同一の XOR マスクで再現した。Python 単体の署名による日本サーバーの端末登録も HTTP 200 / S / 0000 だった。エミュレータは初期設定のみに使用する。車両状態の既存の実車検証は日本版 IONIQ 5 に限定する。

CFB によるローカル生成は[既存の地域共通実装](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api/blob/master/hyundai_kia_connect_api/ApiImplType1.py)と同種の方式であり、新しい実行基盤を追加せずに既存構造を活用できる。

## 影響

- CFB がある場合は、既存の Stamp URL が保存されていても通信しない。
- CFB がない旧設定では、外部サービスを互換経路として引き続き利用できる。
- 署名用データはアプリ ID に対応する。将来のアプリ/API 変更時に再検証や再設定が必要になりうる。
- 認証・車両状態変換・既存の米国 API は引き続き既存構造を使用する。他車種や遠隔操作を新たに検証済みとは扱わない。

## 韓国仕様の確認

韓国には公式の Hyundai Developers API がある。[公開ガイド](https://developers.hyundai.com/web/v1/hyundai/guide_api)では、プロジェクトの Client ID/Secret と車両アクセスへの同意を用いて OAuth の Access/Refresh Token を取得する。[走行距離 API](https://developers.hyundai.com/web/v1/hyundai/specification/data/status_odometer)の公開ヘッダーは Bearer 認証で、Stamp は要求されていない。

これは韓国の開発者向け公開 API の仕様であり、韓国の公式アプリ内部 API が同一方式であることや、日本の車両へ転用できることを示すものではない。

[韓国向け HA 統合](https://github.com/fregmented/hass-bluelink-kr)も Hyundai Developers のプロジェクト登録と OAuth を使い、[データ取得実装](https://github.com/fregmented/hass-bluelink-kr/blob/main/custom_components/bluelink_kr/api.py)は Bearer 認証のみを送る。同統合は韓国国外の車両を対象外としている。
