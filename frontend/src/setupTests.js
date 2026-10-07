import '@testing-library/jest-dom';
import { TextDecoder, TextEncoder } from 'util';

// jsdom (CRA's Jest environment) lacks these; streamAsk needs them.
global.TextDecoder = TextDecoder;
global.TextEncoder = TextEncoder;
