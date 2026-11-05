struct Decision {
    bit: u8,
    position: usize,
    accepted: bool,
    sample: usize,
}

fn word_at(trace: &[Decision], end: usize) -> u32 {
    trace[end + 1 - 26..=end].iter().fold(0, |word, d| (word << 1) | d.bit as u32)
}

fn report(pass: &str, lane: &str, trace: &[Decision], end: usize, position: usize) {
    println!("{pass},{lane},{},{position},{:04x}", trace[end].sample, word_at(trace, end) >> 10);
}

fn inspect(lane: &str, trace: &[Decision]) {
    let mut recovered = vec![false; trace.len()];
    for (end, decision) in trace.iter().enumerate().filter(|(_, d)| d.accepted) {
        report("first", lane, trace, end, decision.position);
        // A fully accepted A/B/C/D group fixes the preceding block boundaries.
        if decision.position != 3 || end < 103 { continue; }
        if !(0..4).all(|p| {
            let d = &trace[end - (3 - p) * 26];
            d.accepted && d.position == p
        }) { continue; }

        let mut previous_valid = None;
        for back in 4..12 {
            let Some(i) = end.checked_sub(back * 26) else { break };
            if i < 25 { break; }
            let position = (3 + 4 - back % 4) % 4;
            let valid = rds_offset_matches(position, rds_syndrome(word_at(trace, i)));
            if valid {
                // Require two adjacent checkword-valid blocks in sequence.
                if let Some((j, p)) = previous_valid {
                    for (idx, pos) in [(j, p), (i, position)] {
                        if !trace[idx].accepted && !recovered[idx] {
                            report("recovered", lane, trace, idx, pos);
                            recovered[idx] = true;
                        }
                    }
                }
                previous_valid = Some((i, position));
            } else {
                previous_valid = None;
            }
        }
    }
}

fn replay(path: &std::path::Path) -> Result<(), Box<dyn std::error::Error>> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut header = [0u8; 20];
    file.read_exact(&mut header)?;
    let number = |i| u32::from_le_bytes(header[i..i+4].try_into().unwrap());
    let count = number(16) as usize;
    if &header[..8] != b"ROSEMPX1" || number(8) != SDR_SAMPLE_RATE_HZ ||
        number(12) != CHANNEL_DECIM as u32 || count == 0 ||
        count > 4 * SDR_SAMPLE_RATE_HZ as usize / CHANNEL_DECIM ||
        file.metadata()?.len() != 20 + count as u64 * 4 {
        return Err("invalid or incomplete ROSE MPX capture".into());
    }
    let rate = SDR_SAMPLE_RATE_HZ as f32 / CHANNEL_DECIM as f32;
    let mut pll = PilotPll::new(rate);
    let mut channel = RdsChannel::new(rate);
    let mut input = std::io::BufReader::new(file);
    for sample in 0..count {
        let mut bytes = [0; 4];
        input.read_exact(&mut bytes)?;
        let mpx = f32::from_le_bytes(bytes);
        if !mpx.is_finite() { return Err("non-finite multiplex sample".into()); }
        pll.process(mpx);
        let (c, s) = pll.subcarrier();
        let lengths = (channel.group_a.trace.len(), channel.group_b.trace.len());
        channel.push(mpx, c, s);
        for (group, before) in [(&mut channel.group_a, lengths.0), (&mut channel.group_b, lengths.1)] {
            if group.trace.len() > before { group.trace[before].sample = sample; }
        }
    }
    println!("pass,lane,end_sample,position,word");
    inspect("a", &channel.group_a.trace);
    inspect("b", &channel.group_b.trace);
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = std::env::args_os().nth(1).ok_or("usage: rds-replay CLIP.mpx")?;
    replay(std::path::Path::new(&path))
}
