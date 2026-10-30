// tb_picosoc_spimemio: REAL ready-handshake test for picosoc spimemio.
// A behavioral SPI flash answers read command 0x03 with canned data
// byte(k) = (addr+k) & 0xFF, so sequential read of 0x00 returns
// 0x03020100 and a jump read of 0x10 returns 0x13121110. The TB does a
// sequential read (passes on pristine AND buggy trees) then a jump
// read (passes on pristine; on the ||-bug ready asserts immediately
// with stale data). Prints "tohost: PASS" or "tohost: FAIL 0xdead" +
// "TRAP: ..." + "CYCLES=<n>". Deterministic. Accepts +seed=<n>
// (logged only). Verilator --binary --timing.
module tb_picosoc_spimemio;
	reg clk = 0;
	reg resetn = 0;
	always #5 clk = ~clk;

	reg valid = 0;
	wire ready;
	reg [23:0] addr = 0;
	wire [31:0] rdata;

	wire flash_csb;
	wire flash_clk;
	wire flash_io0_oe, flash_io1_oe, flash_io2_oe, flash_io3_oe;
	wire flash_io0_do, flash_io1_do, flash_io2_do, flash_io3_do;
	reg flash_io0_di = 0;
	wire flash_io1_di;
	reg flash_io2_di = 1;
	reg flash_io3_di = 1;

	reg [3:0] cfgreg_we = 0;
	reg [31:0] cfgreg_di = 0;
	wire [31:0] cfgreg_do;

	spimemio dut (
		.clk(clk),
		.resetn(resetn),
		.valid(valid),
		.ready(ready),
		.addr(addr),
		.rdata(rdata),
		.flash_csb(flash_csb),
		.flash_clk(flash_clk),
		.flash_io0_oe(flash_io0_oe),
		.flash_io1_oe(flash_io1_oe),
		.flash_io2_oe(flash_io2_oe),
		.flash_io3_oe(flash_io3_oe),
		.flash_io0_do(flash_io0_do),
		.flash_io1_do(flash_io1_do),
		.flash_io2_do(flash_io2_do),
		.flash_io3_do(flash_io3_do),
		.flash_io0_di(flash_io0_di),
		.flash_io1_di(flash_io1_di),
		.flash_io2_di(flash_io2_di),
		.flash_io3_di(flash_io3_di),
		.cfgreg_we(cfgreg_we),
		.cfgreg_di(cfgreg_di),
		.cfgreg_do(cfgreg_do)
	);

	/* verilator lint_off UNUSED */
	wire [31:0] unused_cfg = cfgreg_do;
	wire unused_oe = &{flash_io0_oe, flash_io1_oe, flash_io2_oe,
		flash_io3_oe, flash_io1_do, flash_io2_do, flash_io3_do};
	/* verilator lint_on UNUSED */

	// Behavioral SPI flash: single-bit mode (MOSI = io0, MISO = io1).
	// Frames on csb; shifts MOSI on flash_clk rising; drives MISO on
	// flash_clk falling (stable for the master's rising sample).
	reg [7:0] f_cmd = 0;
	reg [23:0] f_addr = 0;
	reg [7:0] f_out = 0;
	integer f_bits = 0;
	integer f_phase = 0; // 0 idle, 1 cmd, 2 addr, 3 data
	integer f_abit = 0;
	integer f_obit = 0;
	reg f_clk_d = 0;
	reg f_csb_d = 1;

	// MISO combinational from model state: stable for the master's
	// rising-edge sample, tri-stated (pulled high) when deselected.
	assign flash_io1_di = (!flash_csb && f_phase == 3) ?
	                      f_out[7 - f_obit] : 1'b1;

	// Single driver for ALL model state: edge-detect flash_clk in the
	// sys-clk domain. MOSI sampled on flash rising edges (master
	// launches on falling, stable at rising); bit advance computed on
	// flash falling edges (master samples MISO on rising).
	always @(posedge clk) begin
		f_clk_d <= flash_clk;
		f_csb_d <= flash_csb;
		if (f_csb_d && !flash_csb) begin
			f_bits <= 0;
			f_abit <= 0;
			f_obit <= 0;
			f_phase <= 1;
		end else if (!flash_csb) begin
			if (!f_clk_d && flash_clk) begin
				if (f_phase == 1) begin
					f_cmd <= {f_cmd[6:0], flash_io0_do};
					f_bits <= f_bits + 1;
					if (f_bits == 7) begin
						if ({f_cmd[6:0], flash_io0_do} == 8'h03) begin
							f_phase <= 2;
							f_addr <= 0;
							f_abit <= 0;
						end else begin
							f_phase <= 0;
						end
						f_bits <= 0;
					end
				end else if (f_phase == 2) begin
					f_addr <= {f_addr[22:0], flash_io0_do};
					f_abit <= f_abit + 1;
					if (f_abit == 23) begin
						f_phase <= 3;
						f_out <= {f_addr[6:0], flash_io0_do};
						f_obit <= 0;
					end
				end
			end else if (f_clk_d && !flash_clk) begin
				if (f_phase == 3) begin
					if (f_obit == 7) begin
						f_obit <= 0;
						f_addr <= f_addr + 1;
						f_out <= f_addr[7:0] + 8'h01;
					end else begin
						f_obit <= f_obit + 1;
					end
				end
			end
		end
	end

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	int seed_val = 0;
	int unsigned fails = 0;
	reg [31:0] got = 0;

	task automatic wait_ready(input int unsigned limit);
		int unsigned n = 0;
		while (!ready && n < limit) begin
			@(posedge clk);
			n = n + 1;
		end
		if (!ready) begin
			$display("TRAP: spimemio ready timeout");
			fails = fails + 1;
		end
	endtask

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_picosoc_spimemio seed %0d", seed_val);
		$display("- spimemio handshake: seq read then jump read");
		repeat (20) @(posedge clk);
		resetn = 1;
		repeat (10) @(posedge clk);
		// sequential read at 0x00 (passes on pristine AND buggy trees)
		addr = 24'h00;
		@(posedge clk);
		valid = 1;
		wait_ready(30000);
		got = rdata;
		if (got == 32'h03020100) begin
			$display("- ok seq read addr=0x00 data=0x%08h", got);
		end else begin
			$display("TRAP: spimemio seq read got 0x%08h want 0x03020100", got);
			fails = fails + 1;
		end
		valid = 0;
		repeat (10) @(posedge clk);
		// jump read at 0x10: pristine ready stays low until the new
		// transfer completes; the ||-bug asserts ready at once.
		addr = 24'h10;
		@(posedge clk);
		valid = 1;
		@(posedge clk);
		@(posedge clk);
		if (ready) begin
			$display("TRAP: spimemio ready stale hit addr=0x10 rd_addr=0x00");
			$display("TRAP: spimemio jump read got stale data");
			fails = fails + 1;
		end
		wait_ready(30000);
		got = rdata;
		if (got == 32'h13121110) begin
			$display("- ok jump read addr=0x10 data=0x%08h", got);
		end else begin
			$display("TRAP: spimemio jump read got 0x%08h want 0x13121110", got);
			fails = fails + 1;
		end
		valid = 0;
		if (fails == 0) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			$display("TRAP: spimemio %0d check(s) failed", fails);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
