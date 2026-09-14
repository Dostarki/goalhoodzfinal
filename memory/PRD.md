# GoalHoodz (Futbot League) – PRD

## Orijinal istek
- https://github.com/Dostarki/goalhoodz14eylul reposunu çek, çalıştır, güncellemeler yap.
- Cüzdan bağlandıktan sonra çıkan "Entry Fee" penceresinde metin: "Claim the airdrop that has been sent to your account.", buton: CLAIM.
- NFT sahibi olma şartı kaldırılsın; bağlanan her cüzdan claim ekranını görsün.

## Mimari
- Backend: FastAPI (`/app/backend/server.py`, `nft_gate.py`, `nft_traits.py`), Motor/MongoDB.
- Frontend: React + wagmi/RainbowKit (`/app/frontend/src`). Gate UI: `components/WalletGate.jsx`, auth: `context/AuthContext.jsx`.

## Yapılanlar (14 Eyl 2026)
- Repo kuruldu, .env'ler kullanıcı anahtarlarıyla oluşturuldu.
- `WalletGate.jsx`: Entry Fee metni + CLAIM butonu; tekrarlanan açıklama satırı kaldırıldı.
- `server.py`: `/auth/connect`, `/auth/verify`, `current_user` NFT kontrolü kaldırıldı. NFT'siz cüzdan login → `/me` OK (curl ile doğrulandı).
- DB: Atlas kümesi konteyner IP'sini engelliyor → `MONGO_URL` GEÇİCİ olarak `mongodb://127.0.0.1:27017`. Atlas string yedeği: `/app/memory/atlas_env_backup.txt`.

## Bekleyen
- Atlas Network Access `0.0.0.0/0` etkin olduğunda `MONGO_URL`'i yedekten geri al ve backend'i restart et.
- Ana sayfa metinleri hâlâ "holders-only / NFT VERIFIED" diyor (kullanıcı isterse güncellenir).
