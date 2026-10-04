/**
 * Payment Success Receipt Screen Tests
 *
 * Acceptance criteria covered (issue #82):
 *  AC1 – Receipt shows the transaction hash.
 *  AC2 – Receipt shows the payment amount.
 *  AC3 – Receipt shows the destination address.
 *  AC4 – Receipt provides navigation back to wallet or activity.
 *  AC5 – Explorer link is shown where available (and hidden when not).
 *  AC6 – Secret key information is never rendered on the receipt.
 *  AC7 – Copy is honest about confirmation, not a vague "submitted" claim (issue #253).
 */

import React from 'react';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { Alert, Linking } from 'react-native';

// ─── Module mocks ─────────────────────────────────────────────────────────────

jest.mock('expo-router');
jest.mock('expo-clipboard', () => ({
  setStringAsync: jest.fn(async () => {}),
}));
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
jest.mock('lucide-react-native', () => ({
  CheckCircle: () => null,
  Copy: () => null,
  Check: () => null,
  ExternalLink: () => null,
}));

// ─── Typed mock imports ───────────────────────────────────────────────────────

import { useLocalSearchParams, useRouter } from 'expo-router';
import * as Clipboard from 'expo-clipboard';

const mockUseLocalSearchParams = useLocalSearchParams as jest.MockedFunction<typeof useLocalSearchParams>;
const mockUseRouter = useRouter as jest.MockedFunction<typeof useRouter>;

import PaymentSuccessScreen from '../app/payment-success';

// ─── Constants ────────────────────────────────────────────────────────────────

const TX_HASH = 'a1b2c3d4e5f6abcdef1234567890abcdef1234567890abcdef1234567890ab';
const AMOUNT = '25';
const DESTINATION = 'GBXXXXVALIDSTELLARADDRESS1234567890ABCDEFGHIJKLMNOPQRSTUVWX';

const mockReplace = jest.fn();
const mockPush = jest.fn();

beforeEach(() => {
  jest.clearAllMocks();
  mockUseRouter.mockReturnValue({ back: jest.fn(), push: mockPush, replace: mockReplace } as any);
  mockUseLocalSearchParams.mockReturnValue({
    hash: TX_HASH,
    amount: AMOUNT,
    destination: DESTINATION,
  } as any);
});

// ─────────────────────────────────────────────────────────────────────────────
// AC1/AC2/AC3 – Receipt shows hash, amount, and destination
// ─────────────────────────────────────────────────────────────────────────────

describe('AC1-3 – receipt shows transaction details', () => {
  it('shows the transaction hash', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText(TX_HASH)).toBeTruthy();
  });

  it('shows the payment amount', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText(`${AMOUNT} XLM`)).toBeTruthy();
  });

  it('shows the destination address', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText(DESTINATION)).toBeTruthy();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// AC7 – Honest confirmation copy (issue #253)
// ─────────────────────────────────────────────────────────────────────────────

describe('AC7 – honest confirmation copy', () => {
  it('states the transaction was confirmed, not just vaguely "sent"', () => {
    const { getByText, queryByText } = render(<PaymentSuccessScreen />);
    expect(getByText('Payment Confirmed')).toBeTruthy();
    expect(getByText('Your transaction was confirmed on the network.')).toBeTruthy();
    expect(queryByText('Payment Sent')).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// AC4 – Navigation back to wallet or activity
// ─────────────────────────────────────────────────────────────────────────────

describe('AC4 – navigation back to wallet or activity', () => {
  it('navigates to the wallet tab when "Back to Wallet" is pressed', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    fireEvent.press(getByText('Back to Wallet'));
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('navigates to the activity/history tab when "View Activity" is pressed', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    fireEvent.press(getByText('View Activity'));
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)/history');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// AC5 – Explorer link shown where available
// ─────────────────────────────────────────────────────────────────────────────

describe('AC5 – explorer link', () => {
  it('shows a Stellar Expert explorer link for the default (testnet) network', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText('View on Stellar Expert')).toBeTruthy();
  });

  it('opens the explorer URL when the link is pressed', () => {
    const openURLSpy = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as any);
    const { getByText } = render(<PaymentSuccessScreen />);
    fireEvent.press(getByText('View on Stellar Expert'));
    expect(openURLSpy).toHaveBeenCalledWith(
      `https://stellar.expert/explorer/testnet/tx/${TX_HASH}`,
    );
    openURLSpy.mockRestore();
  });

  it('hides the explorer link when there is no transaction hash', () => {
    mockUseLocalSearchParams.mockReturnValue({
      hash: undefined,
      amount: AMOUNT,
      destination: DESTINATION,
    } as any);
    const { queryByText } = render(<PaymentSuccessScreen />);
    expect(queryByText('View on Stellar Expert')).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// AC6 – Secret key is never shown
// ─────────────────────────────────────────────────────────────────────────────

describe('AC6 – no secret key information', () => {
  it('does not render any secret-key related label', () => {
    const { queryByText } = render(<PaymentSuccessScreen />);
    expect(queryByText(/secret/i)).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Copy-to-clipboard support
// ─────────────────────────────────────────────────────────────────────────────

describe('copy transaction hash', () => {
  it('explains what the transaction hash is for in non-technical language', () => {
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(
      getByText('This code identifies your payment. Copy it to share or look it up.'),
    ).toBeTruthy();
  });

  it('copies the transaction hash and shows visible success feedback', async () => {
    const { getByLabelText, getByText } = render(<PaymentSuccessScreen />);
    fireEvent.press(getByLabelText('Copy transaction hash'));
    await waitFor(() => {
      expect(Clipboard.setStringAsync).toHaveBeenCalledWith(TX_HASH);
      expect(getByText('Copied')).toBeTruthy();
    });
  });

  it('shows a clear error when the clipboard write fails', async () => {
    const alertSpy = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    (Clipboard.setStringAsync as jest.MockedFunction<typeof Clipboard.setStringAsync>)
      .mockRejectedValueOnce(new Error('Clipboard unavailable'));

    const { getByLabelText } = render(<PaymentSuccessScreen />);
    fireEvent.press(getByLabelText('Copy transaction hash'));

    await waitFor(() => {
      expect(alertSpy).toHaveBeenCalledWith(
        'Copy Failed',
        'Could not copy the transaction ID. Please try again.',
      );
    });

    alertSpy.mockRestore();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Graceful fallback for missing/invalid data
// ─────────────────────────────────────────────────────────────────────────────

describe('graceful handling of missing or invalid data', () => {
  it('handles missing amount gracefully by showing fallback dash without XLM suffix', () => {
    mockUseLocalSearchParams.mockReturnValue({
      hash: TX_HASH,
      amount: undefined,
      destination: DESTINATION,
    } as any);
    const { getAllByText, queryByText } = render(<PaymentSuccessScreen />);
    expect(getAllByText('—').length).toBeGreaterThan(0);
    expect(queryByText('— XLM')).toBeNull();

  });

  it('handles missing date gracefully by showing fallback dash', () => {
    mockUseLocalSearchParams.mockReturnValue({
      hash: TX_HASH,
      amount: AMOUNT,
      destination: DESTINATION,
      date: undefined,
    } as any);
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText('—')).toBeTruthy();
  });

  it('handles invalid date gracefully by showing fallback dash', () => {
    mockUseLocalSearchParams.mockReturnValue({
      hash: TX_HASH,
      amount: AMOUNT,
      destination: DESTINATION,
      date: 'invalid-date-string',
    } as any);
    const { getByText } = render(<PaymentSuccessScreen />);
    expect(getByText('—')).toBeTruthy();
  });
});
