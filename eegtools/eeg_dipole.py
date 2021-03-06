#!/usr/bin/python3

import os, sys
from typing import *
from argparse import ArgumentParser

import mne
from mne import Epochs, Evoked
from mne.cov import compute_covariance, compute_raw_covariance
from mne.io import Raw
from mne.epochs import read_epochs
from mne.evoked import read_evokeds
from mne.minimum_norm import make_inverse_operator, apply_inverse, write_inverse_operator
from mne.minimum_norm.inverse import InverseOperator, apply_inverse_epochs, apply_inverse_raw
from mne.source_estimate import SourceEstimate

from .common import *


DSL_METHODS = [
	'dSPM', 'MNE', 'sLORETA', 'eLORETA',
]

INPUT_TYPES = [
	'raw', 'epochs', 'evokeds'
]

def make_argument_parser() -> ArgumentParser:
	parser = ArgumentParser(description='Dipole source localization tool for M/EEG recordings')
	
	add_freesurfer_options(parser)
	parser.add_argument('input', metavar='FIF_FILE', help='input file with events to attempt to locate')
	parser.add_argument('-t', '--type', help='input file type', **use_first_as_default(INPUT_TYPES))
	parser.add_argument('-f', '--fwd-file', metavar='FWD_FILE', required=True, help='path to forward solution file')
	parser.add_argument('-m', '--method', help='method to compute inverse solution', **use_first_as_default(DSL_METHODS))
	parser.add_argument('-t0', '--begin', metavar='TIME', type=float, help='start of recording frame to analyze')
	parser.add_argument('-tN', '--end', metavar='TIME', type=float, help='end of recording frame to analyze')
	add_output_options(parser)
	add_logging_options(parser)
	add_parallel_options(parser)
	
	noise_opts = parser.add_argument_group('noise options')
	noise_opts.add_argument('-n', '--noise-file', metavar='FIF_FILE', help='file containing empty room measurements')
	noise_opts.add_argument('-n0', '--noise-begin', metavar='TIME', type=float, help='start of recording frame used to calculate noise covariance')
	noise_opts.add_argument('-nN', '--noise-end', metavar='TIME', type=float, help='end of recording frame used to calculate noise covariance')
	
	event_opts = parser.add_argument_group('event options')
	event_opts.add_argument('--stim', metavar='CHANNEL', help='use signals from trigger channel as events')
	event_opts.add_argument('--annotated', metavar='REGEXP', help='use annotations matching REGEXP to mark events')
	event_opts.add_argument('-e0', '--event-begin', metavar='TIME', type=float, default=-0.2, help='offset from event used as epoch start in seconds (usually negative)')
	event_opts.add_argument('-eN', '--event-end', metavar='TIME', type=float, default=.5, help='offset from event used as epoch end in seconds')
	
	return parser

def apply_inverse_operator(inv_op: InverseOperator, raw: Raw = None, epochs: Epochs = None,
                           evoked: Evoked = None, **kwargs) -> SourceEstimate:
	if evoked:
		return apply_inverse(evoked, inv_op, **kwargs)
	elif epochs:
		return apply_inverse_epochs(epochs, inv_op, **kwargs)
	elif raw:
		return apply_inverse_raw(raw, inv_op, **kwargs)

def main() -> Optional[int]:
	parser = make_argument_parser()
	if len(sys.argv) == 1:
		parser.print_usage(sys.stderr)
		parser.exit(-1)
	
	args = parser.parse_args()
	process_logging_options(args)
	process_freesurfer_options(args)
	process_parallel_options(args)
	
	raw, epochs, evoked = None, None, None
	
	if args.type == 'raw':
		raw = mne.io.read_raw(args.input, preload=True)
		raw.set_eeg_reference('average', projection=True)
	elif args.type == 'epochs':
		epochs = read_epochs(args.input)
	elif args.type == 'evoked':
		evoked = read_evokeds(args.input)
	
	if raw and not epochs:
		if args.stim:
			events = mne.find_events(raw, stim_channel=args.stim)
			event_ids = None
		elif args.annotated:
			events, event_ids = mne.events_from_annotations(raw, regexp=args.annotations)
		else:
			events = None
		
		if events:
			begin, end = raw.time_as_index([args.event_begin, args.event_end])
			epochs = mne.Epochs(raw, events, event_ids, tmin=begin, tmax=end, picks='data')
	
	if epochs and not evoked:
		evoked = epochs.average()
	
	fwd = mne.read_forward_solution(args.fwd_file)
	
	if args.noise_file:
		noise_raw = mne.io.read_raw(args.noise_file)
	elif args.noise_begin or args.noise_end:
		noise_raw = raw.copy()
	else:
		noise_raw = None
	
	if noise_raw and args.noise_begin or args.noise_end:
		noise_raw = noise_raw.crop(args.noise_begin or 0, args.noise_end)
	
	if noise_raw:
		noise_cov = compute_raw_covariance(noise_raw, n_jobs=args.jobs or 1)
	elif epochs:
		noise_cov = compute_covariance(epochs, tmax=-0.01, n_jobs=args.jobs or 1)
	
	info = (raw or epochs or evoked).info
	inv_op = make_inverse_operator(info, fwd, noise_cov)
	
	snr = 3 if epochs or evoked else 1
	lambda2 = 1 / snr ** 2
	begin, end = (raw or epochs or evoked).time_as_index([args.begin, args.end])
	stc = apply_inverse_operator(inv_op, raw=raw, epochs=epochs, evoked=evoked,
	                             start=begin, stop=end, lambda2=lambda2, method=args.method)
	
	inv_file = make_output_filename(args.output, 'inv', compress=args.gzip)
	write_inverse_operator(inv_file, inv_op)
	
	stc_file = make_output_filename(args.output, args.method, ext=None)
	stc.save(stc_file)

if __name__ == '__main__':
	try:
		res = main()
		sys.exit(res)
	
	except KeyboardInterrupt:
		print('Interrupted.', file=sys.stderr)
		sys.exit(-1)
	
	except Exception as e:
		print(f'Error: {e}.', file=sys.stderr)
		sys.exit(-1)
