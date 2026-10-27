// tb_picosoc_uart: REAL loopback test for picosoc simpleuart.
// TX sends byte 0xA5; ser_rx is wired to ser_tx (loopback); RX must
// return recv_buf_valid + recv_buf_data == 0xA5 (observed via reg_dat_do).
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." + "CYCLES=<n>".
// Deterministic. Accepts +seed=<n> (logged only). Verilator --binary --timing.
module tb_picosoc_uart;
	reg clk = 0;
	reg resetn = 0;
	always #5 clk = ~clk;

	wire ser_tx;
	reg [3:0] reg_div_we = 0;
	reg [31:0] reg_div_di = 0;
	wire [31:0] reg_div_do;
	reg reg_dat_we = 0;
	reg reg_dat_re = 0;
	reg [31:0] reg_dat_di = 0;
	wire [31:0] reg_dat_do;
	wire reg_dat_wait;

	simpleuart #(.DEFAULT_DIV(64)) dut (
		.clk(clk),
		.resetn(resetn),
		.ser_tx(ser_tx),
		.ser_rx(ser_tx),
		.reg_div_we(reg_div_we),
		.reg_div_di(reg_div_di),
		.reg_div_do(reg_div_do),
		.reg_dat_we(reg_dat_we),
		.reg_dat_re(reg_dat_re),
		.reg_dat_di(reg_dat_di),
		.reg_dat_do(reg_dat_do),
		.reg_dat_wait(reg_dat_wait)
	);

	/* verilator lint_off UNUSED */
	wire [31:0] unused_div = reg_div_do;
	/* verilator lint_on UNUSED */

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	int seed_val = 0;
	byte got = 8'h00;
	bit done = 0;

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_picosoc_uart seed %0d", seed_val);
		$display("- divider 64 loopback send 0xa5");
		repeat (20) @(posedge clk);
		resetn = 1;
		// program baud divider
		@(posedge clk);
		reg_div_we = 4'hf;
		reg_div_di = 32'd64;
		@(posedge clk);
		reg_div_we = 4'h0;
		// flush power-on dummy char (fixed wait: transmitter is busy)
		repeat (2000) @(posedge clk);
		// send 0xA5
		@(posedge clk);
		reg_dat_di = 32'hA5;
		reg_dat_we = 1;
		@(posedge clk);
		reg_dat_we = 0;
		// wait for RX byte (reg_dat_do == ~0 while no data)
		repeat (20000) begin
			@(posedge clk);
			if (reg_dat_do != 32'hFFFFFFFF) break;
		end
		if (reg_dat_do != 32'hFFFFFFFF) begin
			done = 1;
			got = reg_dat_do[7:0];
		end
		if (done && got == 8'hA5) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			if (done)
				$display("TRAP: uart data mismatch got 0x%02x want 0xa5", got);
			else
				$display("TRAP: uart timeout, no byte received");
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
