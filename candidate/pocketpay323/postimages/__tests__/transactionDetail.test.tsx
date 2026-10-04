import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';
import * as Clipboard from 'expo-clipboard';
import { useWalletStore } from '../src/store/walletStore';
import { useLocalSearchParams, useRouter } from 'expo-router';
import TransactionDetailScreen from '../app/transaction/[id]';

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockUseRouterFn = jest.fn(() => ({
  back: mockBack,
  push: mockPush,
  replace: jest.fn(),
}));
const mockUseLocalSearchParamsFn = jest.fn(() => ({ id: 'tx1' }));

jest.mock('expo-clipboard', () => ({
  setStringAsync: jest.fn(),
}));
jest.mock('../src/store/walletStore');
jest.mock('../src/store/appStore', () => {
  const mockUseAppStore = jest.fn((selector) => {
    const mockState = {
      contacts: [],
    };
    return selector ? selector(mockState) : mockState;
  });
  return {
    normalizePublicKey: (key: string) => key.trim().toUpperCase(),
    useAppStore: mockUseAppStore,
  };
});
jest.mock('expo-router', () => ({
  useRouter: () => mockUseRouterFn(),
  useLocalSearchParams: () => mockUseLocalSearchParamsFn(),
  Stack: {
    Screen: () => null,
  },
}));
jest.mock('lucide-react-native', () => ({
  Copy: () => null,
  Check: () => null,
  ArrowLeft: () => null,
  ArrowUpRight: () => null,
  ArrowDownLeft: () => null,
  ExternalLink: () => null,
  AlertCircle: () => null,
  Clock: () => null,
  CheckCircle: () => null,
  XCircle: () => null,
}));

jest.mock('../src/services/stellar', () => ({
  getExplorerTxUrl: jest.fn((hash: string | null) => {
    if (!hash) return null;
    return `https://stellar.expert/explorer/testnet/tx/${hash}`;
  }),
  fetchOperationById: jest.fn().mockResolvedValue(null),
}));

const mockUseWalletStore = useWalletStore as jest.MockedFunction<typeof useWalletStore>;
const mockUseRouter = mockUseRouterFn;
const mockUseLocalSearchParams = mockUseLocalSearchParamsFn;

const mockTx = {
  id: 'tx1',
  type: 'payment',
  from: 'GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H',
  to: 'GDNOEY2L6EGCMAYNZWJN6K3K6TJJKAKNQJQJWY5HXLFY3LJQY7JJ6NVD',
  amount: '50.0000000',
  asset: 'XLM',
  created_at: '2024-01-15T10:30:00Z',
  hash: 'abc123def456abc123def456abc123def456abc123def456abc123def456abcd',
  transaction_successful: true,
  is_pending: false,
};

function setupStore(overrides: Record<string, unknown> = {}) {
  mockUseWalletStore.mockReturnValue({
    publicKey: 'GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H',
    transactions: [mockTx],
    ...overrides,
  } as any);
}

const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);

beforeEach(() => {
  jest.clearAllMocks();
  alertSpy.mockImplementation(() => undefined);
  mockUseRouter.mockReturnValue({ back: mockBack, push: mockPush, replace: jest.fn() } as any);
  mockUseLocalSearchParams.mockReturnValue({ id: 'tx1' });
  setupStore();
});

describe('Transaction Detail Screen', () => {
  it('renders transaction details correctly', () => {
    const { getByTestId, getByText } = render(<TransactionDetailScreen />);

    expect(getByTestId('detail-amount').props.children).toContain('50 XLM');
    expect(getByText('GBRPYHIL2CI3FNQ4BXLFMNDLFJUNPU2HY3ZMFSHONUCEOASW7QC7OX2H')).toBeTruthy();
    expect(getByText('GDNOEY2L6EGCMAYNZWJN6K3K6TJJKAKNQJQJWY5HXLFY3LJQY7JJ6NVD')).toBeTruthy();
    expect(getByText('abc123def456abc123def456abc123def456abc123def456abc123def456abcd')).toBeTruthy();
  });

  it('renders error state when transaction is not found', async () => {
    mockUseLocalSearchParams.mockReturnValue({ id: 'nonexistent' });
    const { getByTestId, getByText } = render(<TransactionDetailScreen />);

    await waitFor(() => {
      expect(getByTestId('error-container')).toBeTruthy();
    });
    expect(getByText('Transaction Not Found')).toBeTruthy();

    const goBackBtn = getByText('Go Back');
    fireEvent.press(goBackBtn);
    expect(mockBack).toHaveBeenCalled();
  });

  it('explains what the transaction hash is for in non-technical language', () => {
    const { getByText } = render(<TransactionDetailScreen />);
    expect(
      getByText('This code identifies your payment. Copy it to share or look it up.'),
    ).toBeTruthy();
  });

  it('copies transaction hash to clipboard and shows success feedback', async () => {
    const mockSetString = Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>;
    mockSetString.mockResolvedValue(true);

    const { getByTestId, getByText } = render(<TransactionDetailScreen />);
    
    const copyHashBtn = getByTestId('copy-hash-btn');
    fireEvent.press(copyHashBtn);

    expect(mockSetString).toHaveBeenCalledWith(mockTx.hash);
    await waitFor(() => {
      expect(getByText('Copied')).toBeTruthy();
    });
  });

  it('copies sender address to clipboard and shows success feedback', async () => {
    const mockSetString = Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>;
    mockSetString.mockResolvedValue(true);

    const { getByTestId, getByText } = render(<TransactionDetailScreen />);
    
    const copySenderBtn = getByTestId('copy-sender-btn');
    fireEvent.press(copySenderBtn);

    expect(mockSetString).toHaveBeenCalledWith(mockTx.from);
    await waitFor(() => {
      expect(getByText('Copied')).toBeTruthy();
    });
  });

  it('copies recipient address to clipboard and shows success feedback', async () => {
    const mockSetString = Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>;
    mockSetString.mockResolvedValue(true);

    const { getByTestId, getByText } = render(<TransactionDetailScreen />);
    
    const copyRecipientBtn = getByTestId('copy-recipient-btn');
    fireEvent.press(copyRecipientBtn);

    expect(mockSetString).toHaveBeenCalledWith(mockTx.to);
    await waitFor(() => {
      expect(getByText('Copied')).toBeTruthy();
    });
  });

  it('handles clipboard copy failure gracefully by showing an alert', async () => {
    const mockSetString = Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>;
    mockSetString.mockRejectedValue(new Error('Clipboard error'));

    const { getByTestId } = render(<TransactionDetailScreen />);
    
    const copyHashBtn = getByTestId('copy-hash-btn');
    fireEvent.press(copyHashBtn);

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith('Copy Failed', 'Failed to copy to clipboard. Please try again.');
    });
  });

  it('displays successful transaction status', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        transaction_successful: true,
        is_pending: false,
      }],
    });

    const { getAllByText } = render(<TransactionDetailScreen />);
    expect(getAllByText('Successful').length).toBeGreaterThanOrEqual(1);
  });

  it('displays pending transaction status', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        is_pending: true,
      }],
    });

    const { getAllByText } = render(<TransactionDetailScreen />);
    expect(getAllByText('Pending').length).toBeGreaterThanOrEqual(1);
  });

  it('displays failed transaction status', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        transaction_successful: false,
        is_pending: false,
      }],
    });

    const { getAllByText } = render(<TransactionDetailScreen />);
    expect(getAllByText('Failed').length).toBeGreaterThanOrEqual(1);
  });

  it('displays and copies memo when present', async () => {
    const mockSetString = Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>;
    mockSetString.mockResolvedValue(true);

    setupStore({
      transactions: [{
        ...mockTx,
        memo: 'Payment for services',
        memo_type: 'text',
      }],
    });

    const { getByText, getByTestId } = render(<TransactionDetailScreen />);
    
    expect(getByText('Payment for services')).toBeTruthy();
    expect(getByText('Memo (text)')).toBeTruthy();

    const copyMemoBtn = getByTestId('copy-memo-btn');
    fireEvent.press(copyMemoBtn);

    expect(mockSetString).toHaveBeenCalledWith('Payment for services');
    await waitFor(() => {
      expect(getByText('Copied')).toBeTruthy();
    });
  });

  it('does not display memo section when memo is absent', () => {
    const { queryByText } = render(<TransactionDetailScreen />);
    expect(queryByText(/Memo/)).toBeNull();
  });

  it('displays explorer link when hash is present', () => {
    const { getByTestId, getByText } = render(<TransactionDetailScreen />);
    
    expect(getByTestId('explorer-link-btn')).toBeTruthy();
    expect(getByText('View on Stellar Explorer')).toBeTruthy();
  });

  it('handles missing transaction hash gracefully', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        hash: '',
        transaction_hash: '',
      }],
    });

    const { queryByTestId } = render(<TransactionDetailScreen />);
    expect(queryByTestId('explorer-link-btn')).toBeNull();
  });

  it('handles missing sender address gracefully', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        from: '',
      }],
    });

    const { queryByTestId } = render(<TransactionDetailScreen />);
    expect(queryByTestId('copy-sender-btn')).toBeNull();
  });

  it('handles missing recipient address gracefully', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        to: '',
      }],
    });

    const { queryByTestId } = render(<TransactionDetailScreen />);
    expect(queryByTestId('copy-recipient-btn')).toBeNull();
  });

  it('handles missing amount gracefully', () => {
    setupStore({
      transactions: [{
        ...mockTx,
        amount: undefined,
      }],
    });

    const { getByTestId } = render(<TransactionDetailScreen />);
    expect(getByTestId('detail-amount').props.children).toContain('N/A');
  });
});
