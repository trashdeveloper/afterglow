// tb_serv_alu: REAL discriminant test for the SERV ALU BOOL LUT.
//
// Historical bug (olofk/serv@727bb40 "Simplify control logic for bool
// ops", 2021-03-14): BOOL_LUT 16'h8E96 made i_bool_op==2'b01 output
// (rs1==op_b) instead of 0, corrupting shift passthrough (o_rd ORs
// result_bool with i_buf, which requires a zero under bool_op 01).
// The fix set the LUT to 16'h8E06. This TB drives the LUT rows
// directly: bool_op 01 must read 0 for every input pair, while the
// xor/or/and rows are sanity checks that pass under both values.
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." + "CYCLES=<n>".
// Deterministic. Accepts +seed=<n> (logged only). Verilator --binary --timing.
module tb_serv_alu;
	reg clk = 0;
	always #5 clk = ~clk;

	reg i_en = 0;
	reg i_cnt0 = 0;
	reg i_sub = 0;
	reg [1:0] i_bool_op = 0;
	reg i_cmp_eq = 0;
	reg i_cmp_sig = 0;
	reg [3:0] i_rd_sel = 0;
	reg i_rs1 = 0;
	reg i_op_b = 0;
	reg i_buf = 0;
	wire o_cmp;
	wire o_rd;

	serv_alu dut (
		.clk(clk),
		.i_en(i_en),
		.i_cnt0(i_cnt0),
		.o_cmp(o_cmp),
		.i_sub(i_sub),
		.i_bool_op(i_bool_op),
		.i_cmp_eq(i_cmp_eq),
		.i_cmp_sig(i_cmp_sig),
		.i_rd_sel(i_rd_sel),
		.i_rs1(i_rs1),
		.i_op_b(i_op_b),
		.i_buf(i_buf),
		.o_rd(o_rd)
	);

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	int seed_val = 0;
	int fails = 0;

	task check(
		input [1:0] bo,
		input a,
		input b,
		input [3:0] sel,
		input exp
	);
		begin
			i_bool_op = bo;
			i_rs1 = a;
			i_op_b = b;
			i_rd_sel = sel;
			repeat (2) @(posedge clk);
			#1;
			if (o_rd !== exp) begin
				$display("TRAP: bool lut mismatch bo=%b rs1=%b op_b=%b got=%b want=%b",
					bo, a, b, o_rd, exp);
				fails = fails + 1;
			end else begin
				$display("- ok bo=%b rs1=%b op_b=%b rd=%b", bo, a, b, o_rd);
			end
		end
	endtask

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_serv_alu seed %0d", seed_val);
		$display("- serv alu bool lut: bo=01 must read 0");
		repeat (4) @(posedge clk);
		// Discriminating: bool_op 01 must output 0 (buggy 8E96 gives 1
		// when rs1 == op_b).
		check(2'b01, 0, 0, 4'b1000, 0);
		check(2'b01, 1, 1, 4'b1000, 0);
		check(2'b01, 0, 1, 4'b1000, 0);
		check(2'b01, 1, 0, 4'b1000, 0);
		// Sanity rows: identical under 8E06 and 8E96, prove the
		// harness drives the LUT (xor / or / and).
		check(2'b00, 1, 0, 4'b1000, 1);
		check(2'b00, 1, 1, 4'b1000, 0);
		check(2'b10, 0, 1, 4'b1000, 1);
		check(2'b10, 0, 0, 4'b1000, 0);
		check(2'b11, 1, 1, 4'b1000, 1);
		check(2'b11, 1, 0, 4'b1000, 0);
		if (fails == 0) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			$display("TRAP: serv alu bool lut %0d check(s) failed", fails);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
