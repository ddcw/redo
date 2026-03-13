#!/usr/bin/env python3
# writen by ddcw @https://github.com/ddcw
# 解析mysql的redolog, 支持5.7/8.0/8.4/9.x

# 解析的时候遇到一个小坑: 使用python3 open(redo), 然后调试,发现MLOG_COMP_REC_INSERT_8027结构始终对不上(之后再来看信息应该是上一轮的redo还没清理掉),看了N遍代码还是没找到原因, 然后不小心退出了python3,再次使用python3查看该部分数据就正常了. (也可能是MLOG_LSN的锅). 差点一口老血吐死了... 8.x就没得这个坑

# 常见的表空间id
# 4294967294 mysql.ibd
# 4294967279 undo001
# 4294967278 undo002
# 0 ibdata1 

import argparse
import struct
import glob
import sys
import os

def _argparse():
	parser = argparse.ArgumentParser(add_help=False,description="parse mysql redo file. https://github.com/ddcw")
	parser.add_argument('--help', '-h', action='store_true', dest="HELP", default=False,  help='show help')
	parser.add_argument('--version', '-v', action='store_true', dest="VERSION", default=False,  help='show version')
	parser.add_argument('--verbose', '-V', action='store_true', dest="VERBOSE", default=False,  help='show detail')
	parser.add_argument('--start-lsn',dest='START_LSN',type=int,help='start lsn')
	parser.add_argument('--stop-lsn',dest='STOP_LSN',type=int,help='stop lsn')
	parser.add_argument('--set',dest='SET_OPTIONS',action='append',help='filter somethings: --set spaceno=4294967279')
	parser.add_argument(dest='FILENAME', help='redo filename', nargs='*')
	if parser.parse_args().VERSION:
		print("ddcw's redoreader VERSION: 0.1 (内测版)")
		sys.exit(0)
	if parser.parse_args().HELP:
		parser.print_help()
		print("Example:")
		print(f'python3 {sys.argv[0]} redolog/#innodb_redo/#ib_redo*')
		print(f'python3 {sys.argv[0]} redolog/#innodb_redo/#ib_redo* --start-lsn=12345')
		print(f'python3 {sys.argv[0]} redolog/#innodb_redo/#ib_redo* --start-lsn=12345 --stop-lsn=567890')
		print()
		sys.exit(0)
	return parser.parse_args()

def rmach_parse_compressed(data):
	val = data[0]
	n = 1
	if val < 0x80:
		n = 1
	elif val < 0xC0:
		val = struct.unpack('>H',data[:2])[0] & 0x3FFF
		n = 2
	elif val < 0xE0:
		val = struct.unpack('>L',b'\x00'+data[:3])[0] & 0x1FFFFF
		n = 3
	elif val < 0xF0:
		val = struct.unpack('>L',data[:4])[0] & 0xFFFFFFF
		n = 4
	elif val < 0xF8:
		val = struct.unpack('>L',data[1:5])[0]
		n = 5
	elif val < 0xFC:
		val = (struct.unpack('>H',data[:2])[0] & 0x3FF) | 0xFFFFFC00
		n = 2
	elif val < 0xFE:
		val = (struct.unpack('>L',b'\x00'+data[:3])[0] & 0x1FFFF) | 0xFFFE0000
		n = 3
	else:
		val = (struct.unpack('>L',b'\x00'+data[1:4]))[0] | 0xFF000000
		n = 4
	return n,val

def REDOLOGHEADER(filename):
	dd = {}
	with open(filename,'rb') as f:
		dd['name'] = os.path.basename(filename)
		dd['filesize'] = os.path.getsize(filename)
		dd['filename'] = filename
		data = f.read(512)
		dd['LOG_HEADER_FORMAT'],dd['LOG_HEADER_PAD1'],dd['LOG_HEADER_START_LSN'] = struct.unpack('>LLQ',data[:16])
		dd['LOG_HEADER_LOG_UUID'] = dd['LOG_HEADER_PAD1']
		dd['LOG_HEADER_STOP_LSN'] = dd['LOG_HEADER_START_LSN'] + filesize
		dd['LOG_HEADER_CREATOR'] = data[16:48].replace(b'\x00',b'').decode()
		data = f.read(512)
		dd['LOG_CHECKPOINT1_NO'],dd['LOG_CHECKPOINT1_LSN'],dd['LOG_CHECKPOINT1_OFFSET'],dd['LOG_CHECKPOINT1_LOG_BUF_SIZE'] = struct.unpack('>4Q',data[:32])
		data = f.read(512)
		#dd['data'] = data
		data = f.read(512)
		dd['LOG_CHECKPOINT2_NO'],dd['LOG_CHECKPOINT2_LSN'],dd['LOG_CHECKPOINT2_OFFSET'],dd['LOG_CHECKPOINT2_LOG_BUF_SIZE'] = struct.unpack('>4Q',data[:32])
		mysql_version = [ int(x) for x in dd['LOG_HEADER_CREATOR'].split()[1].split('.') ]
		dd['MYSQL_VERSION_ID'] = int(f'{mysql_version[0]}0{mysql_version[1]}{mysql_version[2]}')
	return dd

class REDOREADER(object):
	def __init__(self,filename):
		self.dd = REDOLOGHEADER(filename)
		self._flag = 4 if self.dd['MYSQL_VERSION_ID'] > 80031 else 0
		self.f = open(filename,'rb')
		self.offset = 0
		self.logic_blockno = 0
		self._read_block(4)
		self.min_blockno = self.LOG_BLOCK_HDR_NO
		self.init_block(0)
		self.mtr = { # 1-76 但不含3,5,6,7和12
			1:'MLOG_1BYTE',
			2:'MLOG_2BYTES',
			4:'MLOG_4BYTES',
			8:'MLOG_8BYTES',
			9:'MLOG_REC_INSERT_8027',
			10:'MLOG_REC_CLUST_DELETE_MARK_8027',
			11:'MLOG_REC_SEC_DELETE_MARK',
			13:'MLOG_REC_UPDATE_IN_PLACE_8027',
			14:'MLOG_REC_DELETE_8027',
			15:'MLOG_LIST_END_DELETE_8027',
			16:'MLOG_LIST_START_DELETE_8027',
			17:'MLOG_LIST_END_COPY_CREATED_8027',
			18:'MLOG_PAGE_REORGANIZE_8027',
			19:'MLOG_PAGE_CREATE',
			20:'MLOG_UNDO_INSERT',
			21:'MLOG_UNDO_ERASE_END',
			22:'MLOG_UNDO_INIT',
			23:'MLOG_UNDO_HDR_DISCARD',
			24:'MLOG_UNDO_HDR_REUSE',
			25:'MLOG_UNDO_HDR_CREATE',
			26:'MLOG_REC_MIN_MARK',
			27:'MLOG_IBUF_BITMAP_INIT',
			28:'MLOG_LSN',
			29:'MLOG_INIT_FILE_PAGE',
			30:'MLOG_WRITE_STRING',
			31:'MLOG_MULTI_REC_END',
			33:'MLOG_FILE_CREATE',
			34:'MLOG_FILE_RENAME',
			35:'MLOG_FILE_DELETE',
			36:'MLOG_COMP_REC_MIN_MARK',
			37:'MLOG_COMP_PAGE_CREATE',
			38:'MLOG_COMP_REC_INSERT_8027',
			39:'MLOG_COMP_REC_CLUST_DELETE_MARK_8027',
			40:'MLOG_COMP_REC_SEC_DELETE_MARK',
			41:'MLOG_COMP_REC_UPDATE_IN_PLACE_8027',
			42:'MLOG_COMP_REC_DELETE_8027',
			43:'MLOG_COMP_LIST_END_DELETE_8027',
			44:'MLOG_COMP_LIST_START_DELETE_8027',
			45:'MLOG_COMP_LIST_END_COPY_CREATED_8027',
			46:'MLOG_COMP_PAGE_REORGANIZE_8027',
			47:'MLOG_FILE_CREATE2',
			48:'MLOG_ZIP_WRITE_NODE_PTR',
			49:'MLOG_ZIP_WRITE_BLOB_PTR',
			50:'MLOG_ZIP_WRITE_HEADER',
			51:'MLOG_ZIP_PAGE_COMPRESS',
			52:'MLOG_ZIP_PAGE_COMPRESS_NO_DATA_8027',
			53:'MLOG_ZIP_PAGE_REORGANIZE_8027',
			54:'MLOG_FILE_RENAME2',
			55:'MLOG_FILE_NAME',
			56:'MLOG_CHECKPOINT',
			57:'MLOG_PAGE_CREATE_RTREE',
			58:'MLOG_COMP_PAGE_CREATE_RTREE',
			59:'MLOG_INIT_FILE_PAGE2',
			60:'MLOG_TRUNCATE',
			61:'MLOG_INDEX_LOAD',
			62:'MLOG_TABLE_DYNAMIC_META',
			63:'MLOG_PAGE_CREATE_SDI',
			64:'MLOG_COMP_PAGE_CREATE_SDI',
			65:'MLOG_FILE_EXTEND',
			66:'MLOG_TEST',
			67:'MLOG_REC_INSERT',
			68:'MLOG_REC_CLUST_DELETE_MARK',
			69:'MLOG_REC_DELETE',
			70:'MLOG_REC_UPDATE_IN_PLACE',
			71:'MLOG_LIST_END_COPY_CREATED',
			72:'MLOG_PAGE_REORGANIZE',
			73:'MLOG_ZIP_PAGE_REORGANIZE',
			74:'MLOG_ZIP_PAGE_COMPRESS_NO_DATA',
			75:'MLOG_LIST_END_DELETE',
			76:'MLOG_LIST_START_DELETE',
		}

	def init_block(self,n): # 根据逻辑块(当前文件)初始化
		self._read_block(n)
		self.offset = self.LOG_BLOCK_FIRST_REC_GROUP

	def init_lsn(self,lsn): # 根据lsn初始化
		logic_blockno = (lsn - self.dd['LOG_HEADER_START_LSN'])//512 + 4
		logic_offset = lsn%(self.dd['filesize'])%512
		self.init_block(logic_blockno)
		if 12 < logic_offset < 508:
			self.offset = logic_offset

	def _read_block(self,n=-1):
		if n >= 0:
			self.f.seek(n*512,0)
			self.logic_blockno = n
		else:
			self.logic_blockno += 1
		self.data = self.f.read(512)
		if len(self.data) != 512:
			return self.__close__()
		self.LOG_BLOCK_HDR_NO,self.LOG_BLOCK_HDR_DATA_LEN,self.LOG_BLOCK_FIRST_REC_GROUP,self.LOG_BLOCK_EPOCH_NO = struct.unpack('>LHHL',self.data[:12])
		self.offset = 12
		self.LOG_BLOCK_CHECKSUM, = struct.unpack('>L',self.data[-4:])

	def __close__(self):
		self.f.close()

	def read(self,n):
		data = b''
		while n>0:
			if n > (508-self.offset):
				data += self.data[self.offset:-4]
				n -= len(self.data[self.offset:-4])
				self._read_block()
			else:
				data += self.data[self.offset:self.offset+n]
				self.offset += n
				n = 0
		return data

	def mach_parse_compressed(self): # 注意:需要考虑读取的数据不在一个block里面的情况
		tdata = self.data[self.offset:508]
		if len(tdata) > 5:
			offset,val = rmach_parse_compressed(tdata)
			self.offset += offset
			return val
		else:
			current_logic_blockno = self.logic_blockno
			current_offset = self.offset
			self.init_block(current_logic_blockno+1)
			self.offset = 12
			tdata += self.read(6)
			offset,val = rmach_parse_compressed(tdata)
			if offset + current_offset < 508:
				self.init_block(current_logic_blockno)
				self.offset = current_offset + offset
			else:
				overoffset = 12 + (offset - (508-current_offset))
				self.offset = overoffset
			return val

	def mach_read_next_compressed(self):
		return self.mach_parse_compressed()

	def mach_u64_parse_compressed(self):
		return (self.mach_read_next_compressed()<<32)|self.mach_read_from_n(4)

	def mach_parse_u64_much_compressed(self):
		if self.offset == 508: # 可能跨Block
			self._read_block()
			self.offset = 12
		if self.data[self.offset] != 0xFF:
			return self.mach_parse_compressed()
		n = self.mach_parse_compressed()
		n << 32
		return n|self.mach_parse_compressed()

	def mach_read_from_n(self,n=1):
		return int.from_bytes(self.read(n),'big')

	def read_mtr_header(self):
		offset = self.offset
		blockno = self.LOG_BLOCK_HDR_NO
		t = self.mach_read_from_n(1)
		mtr_type = t&127
		mtr_single = True if t>>7 else False
		if mtr_type in [31,62,56]: # 31:MLOG_MULTI_REC_END 62:MLOG_TABLE_DYNAMIC_META 56:MLOG_CHECKPOINT
			spaceno,pageno = (-1,-1)
		else:
			spaceno,pageno = (self.mach_parse_compressed(), self.mach_parse_compressed())
		return {
			'current_blockno':self.logic_blockno,
			'offset':offset,
			#'lsn':offset + blockno*512-512,
			'lsn':offset + self.logic_blockno*512 + self.dd['LOG_HEADER_START_LSN'] - 2048,
			'blockno':blockno,
			'mtr_type':mtr_type,
			'mtr_single':mtr_single,
			'spaceno':spaceno,
			'pageno':pageno
		}

	def read_mtr(self):
		mtr_header = self.read_mtr_header()
		mtr_body = None
		mtr_status = True
		mtr_type = mtr_header['mtr_type']
		mtr_name = self.mtr[mtr_header['mtr_type']] if mtr_type in self.mtr else 'unsupport'
		if mtr_type == 0: # end
			mtr_status = False
		elif mtr_type == 1:
			mtr_body = self.rmlog_1()
		elif mtr_type == 2:
			mtr_body = self.rmlog_2()
		elif mtr_type == 4:
			mtr_body = self.rmlog_4()
		elif mtr_type == 8:
			mtr_body = self.rmlog_8()
		elif mtr_type == 9:
			mtr_body = self.rmlog_9()
		elif mtr_type == 10:
			mtr_body = self.rmlog_10()
		elif mtr_type == 11:
			mtr_body = self.rmlog_11()
		elif mtr_type == 13:
			mtr_body = self.rmlog_13()
		elif mtr_type == 14:
			mtr_body = self.rmlog_14()
		elif mtr_type == 15:
			mtr_body = self.rmlog_15()
		elif mtr_type == 16:
			mtr_body = self.rmlog_16()
		elif mtr_type == 17:
			mtr_body = self.rmlog_17()
		elif mtr_type == 18:
			mtr_body = self.rmlog_18()
		elif mtr_type == 19:
			mtr_body = self.rmlog_19()
		elif mtr_type == 20:
			mtr_body = self.rmlog_20()
		elif mtr_type == 21:
			mtr_body = self.rmlog_21()
		elif mtr_type == 22:
			mtr_body = self.rmlog_22()
		elif mtr_type == 23:
			mtr_body = self.rmlog_23()
		elif mtr_type == 24:
			mtr_body = self.rmlog_24()
		elif mtr_type == 25:
			mtr_body = self.rmlog_25()
		elif mtr_type == 26:
			mtr_body = self.rmlog_26()
		elif mtr_type == 27:
			mtr_body = self.rmlog_27()
		elif mtr_type == 28:
			mtr_body = self.rmlog_28()
		elif mtr_type == 29:
			mtr_body = self.rmlog_29()
		elif mtr_type == 30:
			mtr_body = self.rmlog_30()
		elif mtr_type == 31:
			mtr_body = self.rmlog_31()
		elif mtr_type == 33:
			mtr_body = self.rmlog_33()
		elif mtr_type == 34:
			mtr_body = self.rmlog_34()
		elif mtr_type == 35:
			mtr_body = self.rmlog_35()
		elif mtr_type == 36:
			mtr_body = self.rmlog_36()
		elif mtr_type == 37:
			mtr_body = self.rmlog_37()
		elif mtr_type == 38:
			mtr_body = self.rmlog_38()
		elif mtr_type == 39:
			mtr_body = self.rmlog_39()
		elif mtr_type == 40:
			mtr_body = self.rmlog_40()
		elif mtr_type == 41:
			mtr_body = self.rmlog_41()
		elif mtr_type == 42:
			mtr_body = self.rmlog_42()
		elif mtr_type == 43:
			mtr_body = self.rmlog_43()
		elif mtr_type == 44:
			mtr_body = self.rmlog_44()
		elif mtr_type == 45:
			mtr_body = self.rmlog_45()
		elif mtr_type == 46:
			mtr_body = self.rmlog_46()
		elif mtr_type == 47:
			mtr_body = self.rmlog_47()
		elif mtr_type == 48:
			mtr_body = self.rmlog_48()
		elif mtr_type == 49:
			mtr_body = self.rmlog_49()
		elif mtr_type == 50:
			mtr_body = self.rmlog_50()
		elif mtr_type == 51:
			mtr_body = self.rmlog_51()
		elif mtr_type == 52:
			mtr_body = self.rmlog_52()
		elif mtr_type == 53:
			mtr_body = self.rmlog_53()
		elif mtr_type == 54:
			mtr_body = self.rmlog_54()
		elif mtr_type == 55:
			mtr_body = self.rmlog_55()
		elif mtr_type == 56:
			mtr_body = self.rmlog_56()
		elif mtr_type == 57:
			mtr_body = self.rmlog_57()
		elif mtr_type == 58:
			mtr_body = self.rmlog_58()
		elif mtr_type == 59:
			mtr_body = self.rmlog_59()
		elif mtr_type == 60:
			mtr_body = self.rmlog_60()
		elif mtr_type == 61:
			mtr_body = self.rmlog_61()
		elif mtr_type == 62:
			mtr_body = self.rmlog_62()
		elif mtr_type == 63:
			mtr_body = self.rmlog_63()
		elif mtr_type == 64:
			mtr_body = self.rmlog_64()
		elif mtr_type == 65:
			mtr_body = self.rmlog_65()
		elif mtr_type == 66:
			mtr_body = self.rmlog_66()
		elif mtr_type == 67:
			mtr_body = self.rmlog_67()
		elif mtr_type == 68:
			mtr_body = self.rmlog_68()
		elif mtr_type == 69:
			mtr_body = self.rmlog_69()
		elif mtr_type == 70:
			mtr_body = self.rmlog_70()
		elif mtr_type == 71:
			mtr_body = self.rmlog_71()
		elif mtr_type == 72:
			mtr_body = self.rmlog_72()
		elif mtr_type == 73:
			mtr_body = self.rmlog_73()
		elif mtr_type == 74:
			mtr_body = self.rmlog_74()
		elif mtr_type == 75:
			mtr_body = self.rmlog_75()
		elif mtr_type == 76:
			mtr_body = self.rmlog_76()
		else:
			mtr_status = False
		return {'status':mtr_status,'header':mtr_header,'data':mtr_body,'name':mtr_name,'other':f"mtr_body = self.rmlog_{mtr_header['mtr_type']}"}

	def rmlog_1(self): # MLOG_1BYTE
		return {
			'offset':self.mach_read_from_n(2),
			'value':self.mach_parse_compressed()
		}

	def rmlog_2(self): # MLOG_2BYTES
		return self.rmlog_1()

	def rmlog_4(self): # MLOG_4BYTES
		return self.rmlog_1()

	def rmlog_8(self): # MLOG_8BYTES
		return {
			'offset':self.mach_read_from_n(2),
			'value':self.mach_u64_parse_compressed()
		}

	def rmlog_9(self): # MLOG_REC_INSERT_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.page_cur_parse_insert_rec())
		return index_info

	def rmlog_10(self): # MLOG_REC_CLUST_DELETE_MARK_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.btr_cur_parse_del_mark_set_clust_rec())
		return index_info

	def rmlog_11(self): # MLOG_REC_SEC_DELETE_MARK
		# btr_cur_parse_del_mark_set_sec_rec
		return {'value':self.mach_read_from_n(1),'offset':self.mach_read_from_n(2)}

	def rmlog_13(self): # MLOG_REC_UPDATE_IN_PLACE_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.btr_cur_parse_update_in_place())
		return index_info

	def rmlog_14(self): # MLOG_REC_DELETE_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.page_cur_parse_delete_rec())
		return index_info
		
	def rmlog_15(self): # MLOG_LIST_END_DELETE_8027
		index_info = self.parse_index_8027(False)
		index_info['offset'] = self.mach_read_from_n(2)
		return index_info
		
	def rmlog_16(self): # MLOG_LIST_START_DELETE_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.page_parse_delete_rec_list())
		return index_info
		
	def rmlog_17(self): # MLOG_LIST_END_COPY_CREATED_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.page_parse_copy_rec_list_to_created_page())
		return index_info
		
	def rmlog_18(self): # MLOG_PAGE_REORGANIZE_8027
		index_info = self.parse_index_8027(False)
		index_info.update(self.btr_parse_page_reorganize(False))
		return index_info
		
	def rmlog_19(self): # MLOG_PAGE_CREATE
		return {}
		
	def rmlog_20(self): # MLOG_UNDO_INSERT
		# trx_undo_parse_add_undo_rec
		undo_len = self.mach_read_from_n(2)
		undo_data = self.read(undo_len)
		return {'len':undo_len,'data':undo_data}
		
	def rmlog_21(self): # MLOG_UNDO_ERASE_END
		return {}
		
	def rmlog_22(self): # MLOG_UNDO_INIT
		return {'type':self.mach_parse_compressed()}

	def rmlog_23(self): # MLOG_UNDO_HDR_DISCARD (5.7)
		return {}
		
	def rmlog_24(self): # MLOG_UNDO_HDR_REUSE
		return {'trx_id':self.mach_u64_parse_compressed()}
		
	def rmlog_25(self): # MLOG_UNDO_HDR_CREATE
		return {'trx_id':self.mach_u64_parse_compressed()}
		
	def rmlog_26(self): # MLOG_REC_MIN_MARK
		return {'rec':self.mach_read_from_n(2)}
		
	def rmlog_27(self): # MLOG_IBUF_BITMAP_INIT
		return {}
		
	def rmlog_28(self): # MLOG_LSN current_lsn
		return {'data':self.read_mtr_header()}
		
	def rmlog_29(self): # MLOG_INIT_FILE_PAGE
		return {}
		
	def rmlog_30(self): # MLOG_WRITE_STRING
		offset = self.mach_read_from_n(2)
		length = self.mach_read_from_n(2)
		data = self.read(length)
		return {'offset':offset,'length':length,'data':data}
		
	def rmlog_31(self): # MLOG_MULTI_REC_END
		return {}
		
	def rmlog_32(self): # MLOG_DUMMY_RECORD
		pass
		
	def rmlog_33(self): # MLOG_FILE_CREATE
		flags = self.mach_read_from_n(4)
		filename_len = self.mach_read_from_n(2)
		filename = self.read(filename_len).decode()
		return {'flags':flags,'len':filename_len,'name':filename}
		
	def rmlog_34(self): # MLOG_FILE_RENAME
		from_len = self.mach_read_from_n(2)
		from_name = self.read(from_len).decode()
		to_len = self.mach_read_from_n(2)
		to_name = self.read(to_len).decode()
		return {'from_len':from_len,'from_name':from_name,'to_len':to_len,'to_name':to_name}
		
	def rmlog_35(self): # MLOG_FILE_DELETE
		length = self.mach_read_from_n(2)
		filename = self.read(length)
		return {'length':length,'filename':filename}
		
	def rmlog_36(self): # MLOG_COMP_REC_MIN_MARK
		return {'rec':self.mach_read_from_n(2)}
		
	def rmlog_37(self): # MLOG_COMP_PAGE_CREATE
		return {}
		
	def rmlog_38(self): # MLOG_COMP_REC_INSERT_8027
		index_info = self.parse_index_8027()
		index_info.update(self.page_cur_parse_insert_rec())
		return index_info
		
	def rmlog_39(self): # MLOG_COMP_REC_CLUST_DELETE_MARK_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.btr_cur_parse_del_mark_set_clust_rec())
		return index_info
		
	def rmlog_40(self): # MLOG_COMP_REC_SEC_DELETE_MARK
		return self.parse_index_8027(True)
		
	def rmlog_41(self): # MLOG_COMP_REC_UPDATE_IN_PLACE_8027
		index_info = self.parse_index_8027()
		index_info.update(self.btr_cur_parse_update_in_place())
		return index_info
		
	def rmlog_42(self): # MLOG_COMP_REC_DELETE_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.page_cur_parse_delete_rec())
		return index_info
		
	def rmlog_43(self): # MLOG_COMP_LIST_END_DELETE_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.page_parse_delete_rec_list())
		return index_info
		
	def rmlog_44(self): # MLOG_COMP_LIST_START_DELETE_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.page_parse_delete_rec_list())
		return index_info
		
	def rmlog_45(self): # MLOG_COMP_LIST_END_COPY_CREATED_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.page_parse_copy_rec_list_to_created_page())
		return index_info
		
	def rmlog_46(self): # MLOG_COMP_PAGE_REORGANIZE_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.btr_parse_page_reorganize(False))
		return index_info

	def rmlog_47(self): # MLOG_FILE_CREATE2 (5.7)
		return self.fil_name_parse('MLOG_FILE_CREATE2')
		
	def rmlog_48(self): # MLOG_ZIP_WRITE_NODE_PTR
		return {'offset':self.mach_read_from_n(2),'z_offset':self.mach_read_from_n(2),'data':self.mach_read_from_n(4)}
		
	def rmlog_49(self): # MLOG_ZIP_WRITE_BLOB_PTR
		offset = self.mach_read_from_n(2)
		z_offset = self.mach_read_from_n(2)
		blobptr = self.read(20) #SPACE_ID,PAGENO,BLOB_HEADER,REAL_SIZE = struct.unpack('>3LQ',blobptr)
		return {
			'offset':offset,
			'z_offset':z_offset,
			'blobptr':blobptr,
		}
		
	def rmlog_50(self): # MLOG_ZIP_WRITE_HEADER
		offset = self.mach_read_from_n(1)
		plen = self.mach_read_from_n(1)
		data = self.read(plen)
		return {'offset':offset,'len':plen,'data':data}
		
	def rmlog_51(self): # MLOG_ZIP_PAGE_COMPRESS
		page_size = self.mach_read_from_n(2)
		trailer_size = self.mach_read_from_n(2)
		page_pre = self.mach_read_from_n(4)
		page_next = self.mach_read_from_n(4)
		page_data = self.read(page_size)
		trailer_data = self.read(trailer_size)
		return {
			'page_size':page_size,
			'trailer_size':trailer_size,
			'page_pre':page_pre,
			'page_next':page_next,
			'page_data':page_data,
			'trailer_data':trailer_data,
		}
		
	def rmlog_52(self): # MLOG_ZIP_PAGE_COMPRESS_NO_DATA_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.page_zip_parse_compress_no_data())
		return index_info
		
	def rmlog_53(self): # MLOG_ZIP_PAGE_REORGANIZE_8027
		index_info = self.parse_index_8027(True)
		index_info.update(self.btr_parse_page_reorganize(True))
		return index_info

	def rmlog_54(self): # MLOG_FILE_RENAME2 (5.7)
		return self.fil_name_parse('MLOG_FILE_RENAME2')
		
	def rmlog_55(self): # MLOG_FILE_NAME (5.7)
		return self.fil_name_parse('MLOG_FILE_NAME')
		
	def rmlog_56(self): # MLOG_CHECKPOINT (5.7) # 1+8
		return {'lsn':self.mach_read_from_n(8)}
		
	def rmlog_57(self): # MLOG_PAGE_CREATE_RTREE
		return {}
		
	def rmlog_58(self): # MLOG_COMP_PAGE_CREATE_RTREE
		return {}
		
	def rmlog_59(self): # MLOG_INIT_FILE_PAGE2
		return {}
		
	def rmlog_60(self): # MLOG_TRUNCATE # Disabled for WL6378
		return {'lsn':self.mach_read_from_n(8)}
		
	def rmlog_61(self): # MLOG_INDEX_LOAD
		return {'data':self.mach_read_from_n(8)}
		
	def rmlog_62(self): # MLOG_TABLE_DYNAMIC_META
		return {
			'table_id':self.mach_parse_u64_much_compressed(),
			'version':self.mach_parse_u64_much_compressed(),
			'persister':self.mach_read_from_n(1),
			'autoinc':self.mach_parse_u64_much_compressed(),
		}
		
	def rmlog_63(self): # MLOG_PAGE_CREATE_SDI
		return {}
		
	def rmlog_64(self): # MLOG_COMP_PAGE_CREATE_SDI
		return {}
		
	def rmlog_65(self): # MLOG_FILE_EXTEND
		return {'offset':self.mach_read_from_n(8),'size':self.mach_read_from_n(8)}
		
	def rmlog_66(self): # MLOG_TEST
		mlog_key = self.mach_read_from_n(8)
		mlog_value = self.mach_read_from_n(8)
		mlog_payload_size = self.mach_read_from_n(2)
		mlog_payload = self.read(mlog_payload_size)
		mlog_start_lsn = self.mach_read_from_n(8)
		mlog_end_lsn = self.mach_read_from_n(8)
		return {
			'key':mlog_key,
			'mlog_value':mlog_value,
			'mlog_payload_size':mlog_payload_size,
			'mlog_payload':mlog_payload,
			'mlog_start_lsn':mlog_start_lsn,
			'mlog_end_lsn':mlog_end_lsn
		}
		
	def rmlog_67(self): # MLOG_REC_INSERT
		index_info = self.parse_index()
		index_info.update(self.page_cur_parse_insert_rec(False))
		return index_info
		
	def rmlog_68(self): # MLOG_REC_CLUST_DELETE_MARK
		index_info = self.parse_index()
		index_info.update(self.btr_cur_parse_del_mark_set_clust_rec())
		return index_info
		
	def rmlog_69(self): # MLOG_REC_DELETE
		index_info = self.parse_index()
		index_info['offset'] = self.mach_read_from_n(2)
		return index_info
		
	def rmlog_70(self): # MLOG_REC_UPDATE_IN_PLACE
		index_info = self.parse_index()
		index_info.update(self.btr_cur_parse_update_in_place())
		return index_info
		
	def rmlog_71(self): # MLOG_LIST_END_COPY_CREATED
		index_info = self.parse_index()
		log_data_len = self.mach_read_from_n(4)
		index_info['log_data_len'] = log_data_len
		index_info['data'] = self.read(log_data_len)
		return index_info
		
	def rmlog_72(self): # MLOG_PAGE_REORGANIZE
		index_info = self.parse_index()
		index_info['level'] = 6
		return index_info
		
	def rmlog_73(self): # MLOG_ZIP_PAGE_REORGANIZE
		index_info = self.parse_index()
		index_info.update(self.btr_parse_page_reorganize(True))
		return index_info
		
	def rmlog_74(self): # MLOG_ZIP_PAGE_COMPRESS_NO_DATA
		index_info = self.parse_index()
		index_info.update(self.page_zip_parse_compress_no_data())
		return index_info
		
	def rmlog_75(self): # MLOG_LIST_END_DELETE
		index_info = self.parse_index()
		index_info['offset'] = self.mach_read_from_n(2)
		return index_info
		
	def rmlog_76(self): # MLOG_LIST_START_DELETE
		index_info = self.parse_index()
		index_info['offset'] = self.mach_read_from_n(2)
		return index_info
		
	def parse_index(self):
		index_version = self.mach_read_from_n(1)
		index_flags = self.mach_read_from_n(1)
		cols = self.mach_read_from_n(2)
		inst_cols = self.mach_read_from_n(2) if index_flags&0x04 else None # INSTANT_FLAG 
		n_uniq = self.mach_read_from_n(2)
		index_fields = [ self.mach_read_from_n(2) for i in range(cols) ]
		index_versioned_fields = [ self.mach_read_from_n(2) for i in range(inst_cols) ] if index_flags&0x02 else None # VERSION_FLAG 
		return {
			'index_version':index_version,
			'index_flags':index_flags,
			'cols':cols,
			'inst_cols':inst_cols,
			'n_uniq':n_uniq,
			'index_fields':index_fields,
			'index_versioned_fields':index_versioned_fields
		}

	def parse_index_8027(self,comp=True):
		if comp:
			n = self.mach_read_from_n(2)
			if n & 0x8000 and self.dd['MYSQL_VERSION_ID'] >= 80013:
				n_inst_cols = n & 0x7fff
				n = self.mach_read_from_n(2)
			n_uniq = self.mach_read_from_n(2)
			col = []
			for x in range(n):
				col_len = self.mach_read_from_n(2) # 第1bit是非空标识
				col.append(col_len&2147483647)
			return {'n':n,'n_uniq':n_uniq,'col':col}
		else:
			return {'n':1,'n_uniq':1}

	def page_cur_parse_insert_rec(self,is_short=False):
		if is_short:
			offset = -1
		else:
			offset = self.mach_read_from_n(2)
		end_seg_len = self.mach_parse_compressed()
		index_info = {'offset':offset}
		index_info['end_seg_len'] = end_seg_len>>1
		index_info['info_and_status_bits'] = self.mach_read_from_n(1) if end_seg_len & 0x1 else -1
		index_info['origin_offset'] = self.mach_parse_compressed() if end_seg_len & 0x1 else -1
		index_info['mismatch_index'] = self.mach_parse_compressed() if end_seg_len & 0x1 else -1
		index_info['data'] = self.read(index_info['end_seg_len'])
		return index_info
				
	def page_cur_parse_delete_rec(self):
		return {'offset':self.mach_read_from_n(2)}

	def page_parse_copy_rec_list_to_created_page(self):
		log_data_len = self.mach_read_from_n(4)
		return {'log_data_len':log_data_len,'data':self.read(log_data_len)}

	def page_parse_delete_rec_list(self):
		return {'offset':self.mach_read_from_n(2)}

	def btr_parse_page_reorganize(self,compressed=True):
		level = 6
		if compressed:
			level = self.mach_read_from_n(1)
		return {'level':level}

	def btr_cur_parse_del_mark_set_clust_rec(self):
		return {
			'flags':self.mach_read_from_n(1),
			'val':self.mach_read_from_n(1),
			'pos':self.mach_parse_compressed(),
			'roll_ptr':self.mach_read_from_n(7),
			'trx_id':self.mach_u64_parse_compressed(),
			'offset':self.mach_read_from_n(2)
		}

	def btr_cur_parse_update_in_place(self):
		index_info = {}
		index_info['flags'] = self.mach_read_from_n(1)
		index_info['pos'] = self.mach_parse_compressed()
		index_info['roll_ptr'] = self.mach_read_from_n(7)
		index_info['trx_id'] = self.mach_u64_parse_compressed()
		index_info['rec_offset'] = self.mach_read_from_n(2)
		info_bits = self.mach_read_from_n(1)
		n_fields = self.mach_parse_compressed()
		fields = []
		for i in range(n_fields):
			field_no = self.mach_parse_compressed()
			field_len = self.mach_parse_compressed()
			field_value = self.read(field_len)
			fields.append({'field_no':field_no,'field_len':field_len,'field_value':field_value})
		index_info['info_bits'] = info_bits
		index_info['n_fields'] = n_fields
		index_info['fields'] = fields
		return index_info
		
	def page_zip_parse_compress_no_data(self):
		return {'level':self.mach_read_from_n(1)}

	def fil_name_parse(self,mlog_type):
		if mlog_type == 'MLOG_FILE_CREATE2':
			_ = self.read(4)
		mlen = self.mach_read_from_n(2)
		mname = self.read(mlen)
		rdd = {'mlen':mlen,'mname':mname}
		if mlog_type == 'MLOG_FILE_RENAME2': #MLOG_FILE_RENAME2
			new_len = self.mach_read_from_n(2)
			new_name = self.read(new_len)
			rdd['new_len'] = new_len
			rdd['new_name'] = new_name
		return rdd
			
if __name__ == '__main__':
	parser = _argparse()
	verbose = parser.VERBOSE
	require_filter = False
	filter_kv = {}
	if parser.SET_OPTIONS is not None:
		for x in parser.SET_OPTIONS:
			for y in x.split(';'):
				for z in y.split(','):
					kv = z.split('=')
					if len(kv) == 2:
						filter_kv[kv[0]] = kv[1]
					elif len(kv) == 1 and z != '':
						filter_kv[kv[0]] = True
	if len(filter_kv) > 0:
		require_filter = True
	if verbose:
		for k in filter_kv:
			print('filter:',k,'=',filter_kv[k])
	filename_list = []
	for x in parser.FILENAME:
		for filename in glob.glob(x):
			if os.path.isfile(filename):
				filename_list.append(filename)
			elif os.path.isdir(filename):
				for n in os.listdir(filename):
					nfilename = os.path.join(filename,n)
					if os.path.isfile(nfilename):
						filename_list.append(nfilename)
			else:
				print(filename,'is not exists, skip it')
	if len(filename_list) == 0:
		print('file',*parser.FILENAME,'not exists')
		sys.exit(1)
	# 文件要都相等,且大小是512的整数倍
	filesize = os.path.getsize(filename_list[0])
	if filesize%512:
		print('文件必须是512的倍数')
		sys.exit(2)
	for filename in filename_list:
		if filesize != os.path.getsize(filename):
			print(filename,'文件大小必须要一样')
			sys.exit(3)
	# 先简单处理下:start-lsn
	nfilename_list = []
	for filename in filename_list:
		dd = REDOLOGHEADER(filename)
		if verbose:
			print(filename,end=':\n')
			for k in dd:
				print(f"    {k}{' '*(30-len(k))}: {dd[k]}")
		if (not dd['name'].endswith('_tmp')) and dd['LOG_HEADER_START_LSN'] > 0:
			nfilename_list.append([dd['LOG_HEADER_START_LSN'],dd])	
	_ = nfilename_list.sort()
	if nfilename_list == []:
		print('没得符合要求的文件..')
		sys.exit(4)
	# 初始化start_lsn,stop_lsn
	start_lsn = parser.START_LSN
	stop_lsn = parser.STOP_LSN
	max_lsn = max([ x[0] for x in nfilename_list ]) + filesize
	min_lsn = min([ x[0] for x in nfilename_list ])
	start_lsn = parser.START_LSN if parser.START_LSN is not None else min_lsn
	stop_lsn = parser.STOP_LSN if parser.STOP_LSN is not None else max_lsn
	if not (min_lsn <= start_lsn < stop_lsn <= max_lsn):
		print(f'min_lsn({min_lsn}) <= start_lsn({start_lsn}) < stop_lsn({stop_lsn}) <= max_lsn({max_lsn}) 不成立')
		sys.exit(3)
	if verbose:
		print('')
		print('    min_lsn   : ',min_lsn)
		print('    start_lsn : ',start_lsn)
		print('    stop_lsn  : ',stop_lsn)
		print('    max_lsn   : ',max_lsn)
	current_lsn = start_lsn
	first_lsn = 0
	with open(nfilename_list[0][1]['filename'],'rb') as f:
		_ = f.seek(2048,0)
		while True:
			data = f.read(512)
			if len(data) != 512:
				break
			bhno,bhdl,bhfr,bhep = struct.unpack('>LHHL',data[:12])
			if bhfr > 0:
				first_lsn = f.tell()-512+bhfr+nfilename_list[0][1]['LOG_HEADER_START_LSN']-2048
				break
	current_lsn = max(current_lsn,first_lsn)
	IS_MF = True
	MF_FLAGS = ''
	for fmin_lsn,x in nfilename_list:
		if current_lsn >= fmin_lsn and current_lsn <= x['LOG_HEADER_STOP_LSN']: # 存在要解析的lsn
			redo = REDOREADER(x['filename'])
			redo.init_lsn(current_lsn)
			while True:
				mtr = redo.read_mtr()
				current_lsn = mtr['header']['lsn']
				if current_lsn > stop_lsn:
					break
				if not mtr['status'] and mtr['header']['mtr_type'] == 0:
					break
				if require_filter:
					filter_status = False
					for k in filter_kv:
						if k in mtr:
							if str(mtr[k]) != filter_kv[k]:
								filter_status = True
						elif k in mtr['header']:
							if str(mtr['header'][k]) != filter_kv[k]:
								filter_status = True
								if verbose:
									print('SKIP:header',mtr['header'],mtr['header'][k],str(filter_kv[k]),mtr['header'][k] != str(filter_kv[k]),type(mtr['header'][k]),type(str(filter_kv[k])))
								break
						elif k in mtr['data']:
							if str(mtr['data'][k]) != filter_kv[k]:
								filter_status = True
								if verbose:
									print('SKIP:data',mtr['data'])
								break
					if filter_status:
						continue
				if mtr['header']['mtr_single']:
					MF_FLAGS = '- [S]'
					IS_MF = True
				else:
					if IS_MF:
						MF_FLAGS = '- [M]'
						IS_MF = False
					else:
						MF_FLAGS = '  [M]'
					if mtr['header']['mtr_type'] == 31:
						IS_MF = True
				print(f"\n{MF_FLAGS} file:{x['name']} gblockno:{mtr['header']['blockno']} lsn:{mtr['header']['lsn']} cblockno:{mtr['header']['current_blockno']} offset:{str(mtr['header']['offset']).zfill(3)} name:{mtr['name']}({mtr['header']['mtr_type']})")
				if mtr['status']:
					print(f"    spaceno   : {mtr['header']['spaceno']}")
					print(f"    pageno    : {mtr['header']['pageno']}")
					for k in mtr['data']:
						print(f"    {k+' '*(10-len(k))}: {mtr['data'][k]}")
				else:
					print('FAILED:',mtr)
					break
